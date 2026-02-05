import json
import os
import re
import traceback
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from tkinter import Tk, StringVar, IntVar, BooleanVar, END
from tkinter import filedialog, messagebox
import tkinter as tk
from tkinter import ttk

from docx import Document


APP_TITLE = "Structured Preschool Interview Tool"
DEFAULT_RUBRIC_PATH = Path("rubric.json")
DEFAULT_BASE_DIR = Path("interviews")


class RubricLoader:
    def __init__(self, rubric_path: Path):
        self.rubric_path = Path(rubric_path)
        self.data = self._load()

    def _load(self) -> dict:
        if not self.rubric_path.exists():
            raise FileNotFoundError(f"Rubric file not found: {self.rubric_path}")
        with self.rubric_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        self.validate(data)
        return data

    @staticmethod
    def validate(data: dict) -> None:
        required = ["metadata", "scoring", "tracks", "traits", "absolute_disqualifiers"]
        for key in required:
            if key not in data:
                raise ValueError(f"rubric.json missing required key: {key}")

        if not isinstance(data["traits"], list) or not data["traits"]:
            raise ValueError("rubric.json requires non-empty list: traits")

        for trait in data["traits"]:
            for k in ["id", "name", "priority", "weight", "primary_question", "descriptors", "sample_answers", "applicable_tracks"]:
                if k not in trait:
                    raise ValueError(f"Trait missing '{k}': {trait}")

    def get_trait_by_id(self, trait_id: str) -> dict:
        for t in self.data["traits"]:
            if t["id"] == trait_id:
                return t
        raise KeyError(f"Trait id not found: {trait_id}")

    def get_traits_for_track(self, track_key: str) -> list[dict]:
        return [
            t for t in self.data["traits"]
            if "all" in t.get("applicable_tracks", []) or track_key in t.get("applicable_tracks", [])
        ]


class ScoringEngine:
    @staticmethod
    def evaluate(rubric: dict, track_key: str, trait_results: dict) -> dict:
        traits = [
            t for t in rubric["traits"]
            if "all" in t["applicable_tracks"] or track_key in t["applicable_tracks"]
        ]

        rows = []
        weighted_total = 0
        critical_eq_1 = False
        critical_lt_3 = False
        disqualifier_present = False

        for trait in traits:
            tid = trait["id"]
            state = trait_results.get(tid, {})
            raw = int(state.get("raw_score", 0) or 0)
            weight = int(trait["weight"])
            weighted = raw * weight
            weighted_total += weighted

            dq = bool(state.get("absolute_disqualifier", False))
            if dq:
                disqualifier_present = True

            is_critical = trait["priority"].lower() == "critical"
            if is_critical and raw == 1:
                critical_eq_1 = True
            if is_critical and raw < 3:
                critical_lt_3 = True

            rows.append({
                "trait_id": tid,
                "trait_name": trait["name"],
                "priority": trait["priority"],
                "weight": weight,
                "raw_score": raw,
                "weighted_score": weighted,
                "question_notes": state.get("question_notes", ""),
                "trait_notes": state.get("trait_notes", ""),
                "verbatim_notes": state.get("verbatim_notes", ""),
                "absolute_disqualifier": dq,
                "primary_question": trait["primary_question"],
            })

        max_weighted = int(rubric["tracks"][track_key]["max_weighted_total"])
        pct = (weighted_total / max_weighted) * 100 if max_weighted else 0.0

        locked_rule = None
        if disqualifier_present:
            locked_rule = "Any Absolute Disqualifier observed => Immediate NO HIRE"
        if critical_eq_1:
            locked_rule = "Any Critical trait raw score = 1 => Immediate NO HIRE"

        if disqualifier_present or critical_eq_1:
            outcome = "No Hire"
        else:
            if pct >= 80 and not critical_lt_3:
                outcome = "Hire"
            elif 65 <= pct <= 79 and not critical_eq_1:
                outcome = "Borderline"
            else:
                outcome = "No Hire"

        return {
            "rows": rows,
            "weighted_total": weighted_total,
            "max_weighted_total": max_weighted,
            "percent_of_max": round(pct, 2),
            "critical_eq_1": critical_eq_1,
            "critical_lt_3": critical_lt_3,
            "disqualifier_present": disqualifier_present,
            "locked_rule": locked_rule,
            "outcome": outcome,
        }


class DraftManager:
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.drafts_dir = self.base_dir / "drafts"
        self.final_dir = self.base_dir / "final"
        self.drafts_dir.mkdir(parents=True, exist_ok=True)
        self.final_dir.mkdir(parents=True, exist_ok=True)

    def save_draft(self, payload: dict) -> Path:
        candidate = payload.get("candidate", {}).get("name", "Unknown")
        safe = sanitize_filename(candidate or "Unknown")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = self.drafts_dir / f"draft-{stamp}-{safe}.json"
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        return path

    def load_draft(self, path: Path) -> dict:
        with Path(path).open("r", encoding="utf-8") as f:
            return json.load(f)


class DocxExporter:
    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def export(self, rubric: dict, payload: dict, scoring: dict) -> Path:
        candidate = payload["candidate"]
        cname = candidate["name"]
        interview_date = candidate["interview_date"]
        track_key = candidate["track"]
        track_label = rubric["tracks"][track_key]["label"]

        doc = Document()
        doc.add_heading("Structured Behavioral Interview Report", level=1)
        doc.add_paragraph(f"Candidate Name: {cname}")
        doc.add_paragraph(f"Interview Date: {interview_date}")
        doc.add_paragraph(f"Track: {track_label}")

        doc.add_heading("Score Summary", level=2)
        table = doc.add_table(rows=1, cols=5)
        hdr = table.rows[0].cells
        hdr[0].text = "Trait"
        hdr[1].text = "Priority"
        hdr[2].text = "Weight"
        hdr[3].text = "Raw Score"
        hdr[4].text = "Weighted Score"

        for row in scoring["rows"]:
            cells = table.add_row().cells
            cells[0].text = row["trait_name"]
            cells[1].text = row["priority"]
            cells[2].text = str(row["weight"])
            cells[3].text = str(row["raw_score"])
            cells[4].text = str(row["weighted_score"])

        doc.add_paragraph(f"Weighted Total: {scoring['weighted_total']} / {scoring['max_weighted_total']}")
        doc.add_paragraph(f"Percent of Max: {scoring['percent_of_max']}%")
        doc.add_paragraph(f"Final Outcome: {scoring['outcome']}")

        doc.add_heading("Override Summary", level=2)
        doc.add_paragraph(f"Any Critical trait = 1: {'Yes' if scoring['critical_eq_1'] else 'No'}")
        doc.add_paragraph(f"Any Absolute Disqualifier observed: {'Yes' if scoring['disqualifier_present'] else 'No'}")
        if scoring["locked_rule"]:
            doc.add_paragraph(f"Outcome lock rule: {scoring['locked_rule']}")
        else:
            doc.add_paragraph("Outcome lock rule: None")

        doc.add_heading("Trait-by-Trait Detail", level=2)
        for idx, row in enumerate(scoring["rows"], start=1):
            doc.add_heading(f"{idx}. {row['trait_name']}", level=3)
            doc.add_paragraph(f"Priority: {row['priority']} | Weight: x{row['weight']}")
            doc.add_paragraph(f"Primary Question: {row['primary_question']}")
            doc.add_paragraph(f"Selected Raw Score: {row['raw_score']}")
            doc.add_paragraph(f"Question Notes: {row['question_notes']}")
            doc.add_paragraph(f"Trait Notes: {row['trait_notes']}")
            doc.add_paragraph(f"Verbatim quote/notes: {row['verbatim_notes']}")

        doc.add_heading("Global Disqualifiers", level=2)
        for d in rubric["absolute_disqualifiers"]:
            doc.add_paragraph(f"- {d}")

        doc.add_paragraph("Observed disqualifier evidence (from verbatim notes):")
        evidence_added = False
        for row in scoring["rows"]:
            if row["absolute_disqualifier"] and row["verbatim_notes"].strip():
                doc.add_paragraph(f"- {row['trait_name']}: {row['verbatim_notes'].strip()}")
                evidence_added = True
        if not evidence_added:
            doc.add_paragraph("- None recorded")

        filename = f"{interview_date} - {sanitize_filename(cname)} - Interview.docx"
        out_path = self.output_dir / filename
        doc.save(out_path)
        return out_path


def sanitize_filename(name: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|]+", "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name or "Unknown"


def is_valid_date_yyyy_mm_dd(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


@dataclass
class InterviewState:
    candidate_name: str = ""
    interview_date: str = ""
    track: str = ""
    current_index: int = 0
    trait_inputs: dict = None

    def to_dict(self) -> dict:
        return {
            "candidate": {
                "name": self.candidate_name,
                "interview_date": self.interview_date,
                "track": self.track,
            },
            "current_index": self.current_index,
            "trait_inputs": self.trait_inputs or {},
        }


class InterviewApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1000x760")

        self.settings = {
            "base_dir": str(DEFAULT_BASE_DIR),
        }

        self.rubric_loader = RubricLoader(DEFAULT_RUBRIC_PATH)
        self.rubric = self.rubric_loader.data

        self.state = InterviewState(
            interview_date=date.today().isoformat(),
            trait_inputs={},
        )
        self.active_traits = []

        self.main_frame = ttk.Frame(self)
        self.main_frame.pack(fill="both", expand=True)

        self.show_start_screen()

    def clear_main(self):
        for child in self.main_frame.winfo_children():
            child.destroy()

    def show_start_screen(self):
        self.clear_main()
        frm = ttk.Frame(self.main_frame, padding=20)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text=APP_TITLE, font=("TkDefaultFont", 16, "bold")).pack(pady=10)
        ttk.Button(frm, text="New Interview", command=self.new_interview).pack(fill="x", pady=5)
        ttk.Button(frm, text="Open Draft", command=self.open_draft).pack(fill="x", pady=5)
        ttk.Button(frm, text="Settings", command=self.open_settings).pack(fill="x", pady=5)
        ttk.Button(frm, text="Exit", command=self.destroy).pack(fill="x", pady=5)

    def open_settings(self):
        top = tk.Toplevel(self)
        top.title("Settings")
        top.geometry("640x180")

        path_var = StringVar(value=self.settings["base_dir"])

        row = ttk.Frame(top, padding=10)
        row.pack(fill="x")
        ttk.Label(row, text="Base output folder:").pack(anchor="w")

        entry = ttk.Entry(row, textvariable=path_var)
        entry.pack(fill="x", pady=5)

        def choose_folder():
            folder = filedialog.askdirectory(initialdir=self.settings["base_dir"])
            if folder:
                path_var.set(folder)

        def save_settings():
            self.settings["base_dir"] = path_var.get().strip() or str(DEFAULT_BASE_DIR)
            DraftManager(Path(self.settings["base_dir"]))
            top.destroy()
            messagebox.showinfo("Settings", "Settings saved.")

        btns = ttk.Frame(top, padding=10)
        btns.pack(fill="x")
        ttk.Button(btns, text="Browse", command=choose_folder).pack(side="left")
        ttk.Button(btns, text="Save", command=save_settings).pack(side="right")

    def new_interview(self):
        self.state = InterviewState(
            interview_date=date.today().isoformat(),
            current_index=0,
            trait_inputs={},
        )
        self.active_traits = []
        self.show_candidate_info()

    def open_draft(self):
        dm = DraftManager(Path(self.settings["base_dir"]))
        path = filedialog.askopenfilename(
            title="Open draft",
            initialdir=str(dm.drafts_dir),
            filetypes=[("JSON", "*.json")],
        )
        if not path:
            return

        try:
            payload = dm.load_draft(Path(path))
            cand = payload.get("candidate", {})
            self.state = InterviewState(
                candidate_name=cand.get("name", ""),
                interview_date=cand.get("interview_date", date.today().isoformat()),
                track=cand.get("track", ""),
                current_index=int(payload.get("current_index", 0)),
                trait_inputs=payload.get("trait_inputs", {}),
            )
            self.active_traits = self.rubric_loader.get_traits_for_track(self.state.track)
            if self.state.current_index <= 0:
                self.show_candidate_info()
            else:
                self.show_trait_screen(self.state.current_index - 1)
        except Exception as e:
            messagebox.showerror("Open Draft Error", f"Could not load draft:\n{e}")

    def show_candidate_info(self):
        self.clear_main()
        frm = ttk.Frame(self.main_frame, padding=20)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Step 1: Candidate Info", font=("TkDefaultFont", 14, "bold")).pack(anchor="w", pady=8)

        name_var = StringVar(value=self.state.candidate_name)
        date_var = StringVar(value=self.state.interview_date or date.today().isoformat())
        track_var = StringVar(value=self.state.track)

        ttk.Label(frm, text="Candidate Name (required)").pack(anchor="w")
        ttk.Entry(frm, textvariable=name_var).pack(fill="x", pady=4)

        ttk.Label(frm, text="Interview Date YYYY-MM-DD (required)").pack(anchor="w")
        ttk.Entry(frm, textvariable=date_var).pack(fill="x", pady=4)

        ttk.Label(frm, text="Track (required)").pack(anchor="w")
        tracks = [(k, self.rubric["tracks"][k]["label"]) for k in self.rubric["tracks"].keys()]
        for k, label in tracks:
            ttk.Radiobutton(frm, text=label, variable=track_var, value=k).pack(anchor="w")

        threshold_lbl = ttk.Label(frm, text="")
        threshold_lbl.pack(anchor="w", pady=10)

        def refresh_thresholds(*_):
            t = track_var.get()
            if t and t in self.rubric["tracks"]:
                cfg = self.rubric["tracks"][t]
                text = (
                    f"Max weighted total: {cfg['max_weighted_total']} | "
                    f"Hire >= {cfg['thresholds']['hire_percent_min']}% | "
                    f"Borderline {cfg['thresholds']['borderline_percent_min']}-{cfg['thresholds']['borderline_percent_max']}% | "
                    f"No Hire < {cfg['thresholds']['borderline_percent_min']}%"
                )
                threshold_lbl.config(text=text)
            else:
                threshold_lbl.config(text="")

        track_var.trace_add("write", refresh_thresholds)
        refresh_thresholds()

        def go_next():
            name = name_var.get().strip()
            iv_date = date_var.get().strip()
            track = track_var.get().strip()

            if not name:
                messagebox.showerror("Validation", "Candidate Name is required.")
                return
            if not is_valid_date_yyyy_mm_dd(iv_date):
                messagebox.showerror("Validation", "Interview Date must be YYYY-MM-DD.")
                return
            if not track:
                messagebox.showerror("Validation", "Track selection is required.")
                return

            self.state.candidate_name = name
            self.state.interview_date = iv_date
            self.state.track = track

            self.active_traits = self.rubric_loader.get_traits_for_track(track)
            for trait in self.active_traits:
                self.state.trait_inputs.setdefault(trait["id"], {
                    "raw_score": None,
                    "question_notes": "",
                    "trait_notes": "",
                    "verbatim_notes": "",
                    "absolute_disqualifier": False,
                })

            self.state.current_index = 1
            self.show_trait_screen(0)

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=10)
        ttk.Button(btns, text="Back to Start", command=self.show_start_screen).pack(side="left")
        ttk.Button(btns, text="Next", command=go_next).pack(side="right")

    def show_disqualifier_reference(self):
        top = tk.Toplevel(self)
        top.title("Absolute Disqualifiers")
        top.geometry("760x320")
        text = tk.Text(top, wrap="word")
        text.pack(fill="both", expand=True)
        text.insert(END, "Absolute Disqualifiers (Global)\n\n")
        for item in self.rubric["absolute_disqualifiers"]:
            text.insert(END, f"- {item}\n")
        text.config(state="disabled")

    def show_trait_screen(self, idx: int):
        self.clear_main()
        if idx < 0 or idx >= len(self.active_traits):
            self.show_candidate_info()
            return

        trait = self.active_traits[idx]
        tid = trait["id"]
        state = self.state.trait_inputs.setdefault(tid, {
            "raw_score": None,
            "question_notes": "",
            "trait_notes": "",
            "verbatim_notes": "",
            "absolute_disqualifier": False,
        })

        frm = ttk.Frame(self.main_frame, padding=12)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text=f"Trait {idx + 1} of {len(self.active_traits)}", font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        ttk.Label(frm, text=trait["name"], font=("TkDefaultFont", 14, "bold")).pack(anchor="w", pady=4)
        ttk.Label(frm, text=f"Priority: {trait['priority']} | Weight: x{trait['weight']}").pack(anchor="w")
        ttk.Label(frm, text=f"Primary Question: {trait['primary_question']}", wraplength=960).pack(anchor="w", pady=6)

        ladder_frame = ttk.LabelFrame(frm, text="Scoring descriptors (1-5)")
        ladder_frame.pack(fill="x", pady=4)
        for n in [5, 4, 3, 2, 1]:
            ttk.Label(ladder_frame, text=f"{n}: {trait['descriptors'][str(n)]}", wraplength=950).pack(anchor="w")

        sample_frame = ttk.LabelFrame(frm, text="Sample answers (display only)")
        sample_frame.pack(fill="x", pady=4)
        for n in [5, 4, 3, 2, 1]:
            ttk.Label(sample_frame, text=f"{n}: {trait['sample_answers'][str(n)]}", wraplength=950).pack(anchor="w")

        raw_var = IntVar(value=int(state["raw_score"]) if state.get("raw_score") else 0)
        dq_var = BooleanVar(value=bool(state.get("absolute_disqualifier", False)))

        score_row = ttk.Frame(frm)
        score_row.pack(fill="x", pady=6)
        ttk.Label(score_row, text="Raw score (required):").pack(side="left")
        for n in [1, 2, 3, 4, 5]:
            ttk.Radiobutton(score_row, text=str(n), value=n, variable=raw_var).pack(side="left")

        notes_frame = ttk.Frame(frm)
        notes_frame.pack(fill="both", expand=True)

        ttk.Label(notes_frame, text="Question notes").pack(anchor="w")
        q_text = tk.Text(notes_frame, height=4, wrap="word")
        q_text.pack(fill="x", pady=2)
        q_text.insert("1.0", state.get("question_notes", ""))

        ttk.Label(notes_frame, text="Trait-level notes").pack(anchor="w")
        t_text = tk.Text(notes_frame, height=4, wrap="word")
        t_text.pack(fill="x", pady=2)
        t_text.insert("1.0", state.get("trait_notes", ""))

        ttk.Label(notes_frame, text="Verbatim quote / notes for disqualifier logging").pack(anchor="w")
        v_text = tk.Text(notes_frame, height=4, wrap="word")
        v_text.pack(fill="x", pady=2)
        v_text.insert("1.0", state.get("verbatim_notes", ""))

        ttk.Checkbutton(
            notes_frame,
            text="Absolute disqualifier observed in this trait",
            variable=dq_var,
        ).pack(anchor="w", pady=4)

        ttk.Button(notes_frame, text="View Absolute Disqualifiers Reference", command=self.show_disqualifier_reference).pack(anchor="w")

        def persist_state() -> bool:
            raw = raw_var.get()
            qn = q_text.get("1.0", END).strip()
            tn = t_text.get("1.0", END).strip()
            vn = v_text.get("1.0", END).strip()
            dq = dq_var.get()

            if raw not in {1, 2, 3, 4, 5}:
                messagebox.showerror("Validation", "Raw score 1-5 is required.")
                return False

            if dq and not vn:
                messagebox.showerror("Validation", "Verbatim quote/notes is required if disqualifier is checked.")
                return False

            self.state.trait_inputs[tid] = {
                "raw_score": raw,
                "question_notes": qn,
                "trait_notes": tn,
                "verbatim_notes": vn,
                "absolute_disqualifier": dq,
            }
            self.state.current_index = idx + 1
            return True

        def go_back():
            if not persist_state():
                return
            if idx == 0:
                self.show_candidate_info()
            else:
                self.show_trait_screen(idx - 1)

        def go_next():
            if not persist_state():
                return
            if idx + 1 < len(self.active_traits):
                self.show_trait_screen(idx + 1)
            else:
                messagebox.showinfo("Interview", "End of traits reached. You can Finalize now.")

        def save_draft():
            if not persist_state():
                return
            dm = DraftManager(Path(self.settings["base_dir"]))
            payload = self.state.to_dict()
            payload["current_index"] = idx + 1
            path = dm.save_draft(payload)
            messagebox.showinfo("Draft Saved", f"Draft saved to:\n{path}")

        def finalize():
            if not persist_state():
                return
            try:
                self.validate_before_finalize()
                scoring = ScoringEngine.evaluate(self.rubric, self.state.track, self.state.trait_inputs)
                payload = self.state.to_dict()
                exporter = DocxExporter(Path(self.settings["base_dir"]) / "final")
                out_path = exporter.export(self.rubric, payload, scoring)
                messagebox.showinfo(
                    "Finalized",
                    (
                        f"Outcome: {scoring['outcome']}\n"
                        f"Weighted Total: {scoring['weighted_total']}/{scoring['max_weighted_total']}\n"
                        f"Percent: {scoring['percent_of_max']}%\n\n"
                        f"Report saved to:\n{out_path}"
                    ),
                )
                self.show_start_screen()
            except Exception as e:
                messagebox.showerror("Finalize Error", f"{e}\n\n{traceback.format_exc()}")

        nav = ttk.Frame(frm)
        nav.pack(fill="x", pady=8)
        ttk.Button(nav, text="Back", command=go_back).pack(side="left")
        ttk.Button(nav, text="Next", command=go_next).pack(side="left", padx=4)
        ttk.Button(nav, text="Save Draft", command=save_draft).pack(side="left", padx=4)
        ttk.Button(nav, text="Finalize", command=finalize).pack(side="right")
        ttk.Button(nav, text="Exit", command=self.destroy).pack(side="right", padx=4)

    def validate_before_finalize(self):
        if not self.state.candidate_name.strip():
            raise ValueError("Candidate Name is required.")
        if not is_valid_date_yyyy_mm_dd(self.state.interview_date.strip()):
            raise ValueError("Interview Date must be valid YYYY-MM-DD.")
        if not self.state.track:
            raise ValueError("Track selection is required.")

        traits = self.rubric_loader.get_traits_for_track(self.state.track)
        for trait in traits:
            tid = trait["id"]
            tstate = self.state.trait_inputs.get(tid)
            if not tstate or tstate.get("raw_score") not in {1, 2, 3, 4, 5}:
                raise ValueError(f"Missing raw score for trait: {trait['name']}")
            if tstate.get("absolute_disqualifier") and not (tstate.get("verbatim_notes") or "").strip():
                raise ValueError(f"Trait '{trait['name']}' has disqualifier checked but no verbatim notes.")


if __name__ == "__main__":
    try:
        app = InterviewApp()
        app.mainloop()
    except Exception as exc:
        messagebox.showerror("Fatal Error", str(exc))
