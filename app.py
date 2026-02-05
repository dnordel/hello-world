import json
import re
import traceback
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from tkinter import StringVar, IntVar, BooleanVar, END
from tkinter import filedialog, messagebox
import tkinter as tk
from tkinter import ttk
from tkinter import font as tkfont

from docx import Document


APP_TITLE = "Structured Preschool Interview Tool"
APP_DIR = Path(__file__).resolve().parent
DEFAULT_RUBRIC_PATH = APP_DIR / "rubric.json"
DEFAULT_SIGNALS_PATH = APP_DIR / "disqualifier_signals.json"
DEFAULT_BASE_DIR = APP_DIR / "interviews"
DEFAULT_FONT_SIZE = 10
MIN_FONT_SIZE = 8
MAX_FONT_SIZE = 18
DEFAULT_SCHOOL_OPTIONS = [
    "Hawthorne",
    "Palmdale",
    "North Long Beach",
]


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

    def get_traits_for_track(self, track_key: str) -> list[dict]:
        return [
            t for t in self.data["traits"]
            if "all" in t.get("applicable_tracks", []) or track_key in t.get("applicable_tracks", [])
        ]


class DisqualifierSignalLibrary:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = self._load()
        self.by_trait_id = self._build_index()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"questions": []}
        with self.path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _build_index(self) -> dict[str, dict]:
        out = {}
        for q in self.data.get("questions", []):
            raw_trait = str(q.get("trait_id", "")).strip()
            if raw_trait.isdigit():
                out[f"trait_{raw_trait}"] = q
            elif raw_trait:
                out[raw_trait] = q
        return out

    def get_for_trait(self, trait_id: str) -> dict | None:
        return self.by_trait_id.get(trait_id)


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
        school = candidate.get("school", "")
        track_label = rubric["tracks"][track_key]["label"]

        doc = Document()
        doc.add_heading("Structured Behavioral Interview Report", level=1)
        doc.add_paragraph(f"Candidate Name: {cname}")
        doc.add_paragraph(f"Interview Date: {interview_date}")
        doc.add_paragraph(f"School/Location: {school}")
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
        doc.add_paragraph(f"Outcome lock rule: {scoring['locked_rule'] if scoring['locked_rule'] else 'None'}")

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

        school_part = sanitize_filename(school) if school else "UnknownSchool"
        filename = f"{interview_date} - {school_part} - {sanitize_filename(cname)} - Interview.docx"
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
    school: str = ""
    track: str = ""
    current_index: int = 0
    trait_inputs: dict = None

    def to_dict(self) -> dict:
        return {
            "candidate": {
                "name": self.candidate_name,
                "interview_date": self.interview_date,
                "school": self.school,
                "track": self.track,
            },
            "current_index": self.current_index,
            "trait_inputs": self.trait_inputs or {},
        }


class InterviewApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1100x800")

        self.settings = {
            "base_dir": str(DEFAULT_BASE_DIR),
            "font_size": DEFAULT_FONT_SIZE,
        }
        self.school_options = DEFAULT_SCHOOL_OPTIONS.copy()

        self.rubric_loader = RubricLoader(DEFAULT_RUBRIC_PATH)
        self.rubric = self.rubric_loader.data
        self.signals = DisqualifierSignalLibrary(DEFAULT_SIGNALS_PATH)

        self.state = InterviewState(interview_date=date.today().isoformat(), trait_inputs={})
        self.active_traits = []

        self.apply_font_size(self.settings["font_size"])
        self._build_layout()
        self.show_start_screen()

    def _build_layout(self):
        self.toolbar = ttk.Frame(self)
        self.toolbar.pack(fill="x", padx=8, pady=4)
        ttk.Label(self.toolbar, text="Text Size:").pack(side="left")
        ttk.Button(self.toolbar, text="A-", command=lambda: self.adjust_font_size(-1)).pack(side="left", padx=2)
        self.font_label = ttk.Label(self.toolbar, text=str(self.settings["font_size"]))
        self.font_label.pack(side="left", padx=2)
        ttk.Button(self.toolbar, text="A+", command=lambda: self.adjust_font_size(1)).pack(side="left", padx=2)

        self.main_holder = ttk.Frame(self)
        self.main_holder.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(self.main_holder, highlightthickness=0)
        self.v_scroll = ttk.Scrollbar(self.main_holder, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.v_scroll.set)
        self.v_scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.page_frame = ttk.Frame(self.canvas)
        self.page_window = self.canvas.create_window((0, 0), window=self.page_frame, anchor="nw")

        self.page_frame.bind("<Configure>", self._on_frame_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        self.bind_all("<MouseWheel>", self._on_mousewheel)
        self.bind_all("<Button-4>", self._on_mousewheel)
        self.bind_all("<Button-5>", self._on_mousewheel)


        self.footer_separator = ttk.Separator(self, orient="horizontal")
        self.footer_separator.pack(fill="x")
        self.footer = ttk.Frame(self, padding=(8, 6))
        self.footer.pack(fill="x")

    def _on_frame_configure(self, _event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfig(self.page_window, width=event.width)

    def _on_mousewheel(self, event):
        if getattr(event, "num", None) == 4:
            self.canvas.yview_scroll(-3, "units")
        elif getattr(event, "num", None) == 5:
            self.canvas.yview_scroll(3, "units")
        else:
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def apply_font_size(self, size: int):
        size = max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, int(size)))
        self.settings["font_size"] = size
        for font_name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont"):
            try:
                f = tkfont.nametofont(font_name)
                f.configure(size=size)
            except tk.TclError:
                pass
        if hasattr(self, "font_label"):
            self.font_label.config(text=str(size))

    def adjust_font_size(self, delta: int):
        self.apply_font_size(self.settings["font_size"] + delta)

    def scroll_top(self):
        self.canvas.yview_moveto(0)

    def clear_page(self):
        for child in self.page_frame.winfo_children():
            child.destroy()
        self.clear_footer()
        self.scroll_top()

    def clear_footer(self):
        for child in self.footer.winfo_children():
            child.destroy()

    def set_footer_actions(self, left_actions=None, right_actions=None):
        self.clear_footer()

        left = ttk.Frame(self.footer)
        left.pack(side="left")
        for label, command in (left_actions or []):
            ttk.Button(left, text=label, command=command).pack(side="left", padx=4)

        right = ttk.Frame(self.footer)
        right.pack(side="right")
        for label, command in (right_actions or []):
            ttk.Button(right, text=label, command=command).pack(side="right", padx=4)

    def show_start_screen(self):
        self.clear_page()
        frm = ttk.Frame(self.page_frame, padding=20)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text=APP_TITLE, font=("TkDefaultFont", self.settings["font_size"] + 6, "bold")).pack(pady=10)
        ttk.Button(frm, text="New Interview", command=self.new_interview).pack(fill="x", pady=5)
        ttk.Button(frm, text="Open Draft", command=self.open_draft).pack(fill="x", pady=5)
        ttk.Button(frm, text="Settings", command=self.open_settings).pack(fill="x", pady=5)
        ttk.Button(frm, text="Exit", command=self.destroy).pack(fill="x", pady=5)

    def open_settings(self):
        top = tk.Toplevel(self)
        top.title("Settings")
        top.geometry("650x220")

        path_var = StringVar(value=self.settings["base_dir"])
        size_var = IntVar(value=self.settings["font_size"])

        row = ttk.Frame(top, padding=10)
        row.pack(fill="x")
        ttk.Label(row, text="Base output folder:").pack(anchor="w")
        ttk.Entry(row, textvariable=path_var).pack(fill="x", pady=5)

        font_row = ttk.Frame(top, padding=10)
        font_row.pack(fill="x")
        ttk.Label(font_row, text="Default text size:").pack(side="left")
        ttk.Spinbox(font_row, from_=MIN_FONT_SIZE, to=MAX_FONT_SIZE, textvariable=size_var, width=8).pack(side="left", padx=6)

        def choose_folder():
            folder = filedialog.askdirectory(initialdir=self.settings["base_dir"])
            if folder:
                path_var.set(folder)

        def save_settings():
            self.settings["base_dir"] = path_var.get().strip() or str(DEFAULT_BASE_DIR)
            self.apply_font_size(size_var.get())
            DraftManager(Path(self.settings["base_dir"]))
            top.destroy()
            messagebox.showinfo("Settings", "Settings saved.")

        btns = ttk.Frame(top, padding=10)
        btns.pack(fill="x")
        ttk.Button(btns, text="Browse", command=choose_folder).pack(side="left")
        ttk.Button(btns, text="Save", command=save_settings).pack(side="right")

    def new_interview(self):
        self.state = InterviewState(interview_date=date.today().isoformat(), current_index=0, trait_inputs={})
        self.active_traits = []
        self.show_candidate_info()

    def open_draft(self):
        dm = DraftManager(Path(self.settings["base_dir"]))
        path = filedialog.askopenfilename(title="Open draft", initialdir=str(dm.drafts_dir), filetypes=[("JSON", "*.json")])
        if not path:
            return

        try:
            payload = dm.load_draft(Path(path))
            cand = payload.get("candidate", {})
            self.state = InterviewState(
                candidate_name=cand.get("name", ""),
                interview_date=cand.get("interview_date", date.today().isoformat()),
                school=cand.get("school", ""),
                track=cand.get("track", ""),
                current_index=int(payload.get("current_index", 0)),
                trait_inputs=payload.get("trait_inputs", {}),
            )
            if self.state.school and self.state.school not in self.school_options:
                self.school_options.append(self.state.school)
            self.active_traits = self.rubric_loader.get_traits_for_track(self.state.track) if self.state.track else []
            if self.state.current_index <= 0 or not self.active_traits:
                self.show_candidate_info()
            else:
                self.show_trait_screen(self.state.current_index - 1)
        except Exception as e:
            messagebox.showerror("Open Draft Error", f"Could not load draft:\n{e}")

    def show_candidate_info(self):
        self.clear_page()
        frm = ttk.Frame(self.page_frame, padding=20)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Step 1: Candidate Info", font=("TkDefaultFont", self.settings["font_size"] + 4, "bold")).pack(anchor="w", pady=8)

        name_var = StringVar(value=self.state.candidate_name)
        date_var = StringVar(value=self.state.interview_date or date.today().isoformat())
        school_var = StringVar(value=self.state.school)
        track_var = StringVar(value=self.state.track)

        ttk.Label(frm, text="Candidate Name (required)").pack(anchor="w")
        ttk.Entry(frm, textvariable=name_var).pack(fill="x", pady=4)

        ttk.Label(frm, text="Interview Date YYYY-MM-DD (required)").pack(anchor="w")
        ttk.Entry(frm, textvariable=date_var).pack(fill="x", pady=4)

        ttk.Label(frm, text="School (required)").pack(anchor="w")
        school_row = ttk.Frame(frm)
        school_row.pack(fill="x", pady=4)
        school_combo = ttk.Combobox(school_row, textvariable=school_var, values=self.school_options)
        school_combo.pack(side="left", fill="x", expand=True)

        def add_school():
            value = school_var.get().strip()
            if not value:
                messagebox.showerror("Validation", "Enter a school name before adding.")
                return
            if value not in self.school_options:
                self.school_options.append(value)
            school_combo.configure(values=self.school_options)
            school_var.set(value)

        ttk.Button(school_row, text="Add School", command=add_school).pack(side="left", padx=6)

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
                threshold_lbl.config(
                    text=(
                        f"Max weighted total: {cfg['max_weighted_total']} | "
                        f"Hire >= {cfg['thresholds']['hire_percent_min']}% | "
                        f"Borderline {cfg['thresholds']['borderline_percent_min']}-{cfg['thresholds']['borderline_percent_max']}% | "
                        f"No Hire < {cfg['thresholds']['borderline_percent_min']}%"
                    )
                )
            else:
                threshold_lbl.config(text="")

        track_var.trace_add("write", refresh_thresholds)
        refresh_thresholds()

        def go_next():
            name = name_var.get().strip()
            iv_date = date_var.get().strip()
            school = school_var.get().strip()
            track = track_var.get().strip()

            if not name:
                messagebox.showerror("Validation", "Candidate Name is required.")
                return
            if not is_valid_date_yyyy_mm_dd(iv_date):
                messagebox.showerror("Validation", "Interview Date must be YYYY-MM-DD.")
                return
            if not school:
                messagebox.showerror("Validation", "School selection is required.")
                return
            if not track:
                messagebox.showerror("Validation", "Track selection is required.")
                return

            self.state.candidate_name = name
            self.state.interview_date = iv_date
            self.state.school = school
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

        self.set_footer_actions(
            left_actions=[("Back to Start", self.show_start_screen)],
            right_actions=[("Next", go_next)],
        )

    def show_disqualifier_reference(self):
        top = tk.Toplevel(self)
        top.title("Absolute Disqualifiers")
        top.geometry("760x360")
        text = tk.Text(top, wrap="word")
        text.pack(fill="both", expand=True)
        text.insert(END, "Absolute Disqualifiers (Global)\n\n")
        for item in self.rubric["absolute_disqualifiers"]:
            text.insert(END, f"- {item}\n")
        text.config(state="disabled")

    def _render_signal_examples(self, parent, trait_id: str):
        data = self.signals.get_for_trait(trait_id)
        box = ttk.LabelFrame(parent, text="Disqualifier signal examples (probe prompts)")
        box.pack(fill="both", pady=6, expand=True)

        text = tk.Text(
            box,
            height=14,
            wrap="word",
            font=("TkDefaultFont", self.settings["font_size"]),
        )
        ybar = ttk.Scrollbar(box, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=ybar.set)
        ybar.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)

        if not data:
            text.insert(END, "No signal examples configured for this trait.")
            text.config(state="disabled")
            return

        text.insert(END, f"Question ID: {data.get('question_id', '')}\n")
        text.insert(END, f"Primary question: {data.get('primary_question', '')}\n\n")

        signal_items = data.get("disqualifier_signals") or data.get("signals") or []
        if not signal_items:
            text.insert(END, "No signal examples configured for this trait.")
            text.config(state="disabled")
            return

        for idx, item in enumerate(signal_items, start=1):
            t = item.get("disqualifier_type", "")
            auto = "Yes" if item.get("auto_disqualify_if_confirmed") else "No"
            text.insert(END, f"{idx}. {t}\n")
            text.insert(END, f"   Auto disqualify if confirmed: {auto}\n")
            for ex in item.get("examples", []):
                text.insert(END, f"   - {ex}\n")
            probe = item.get("probe_to_confirm", "")
            if probe:
                text.insert(END, f"   Probe: {probe}\n")
            text.insert(END, "\n")

        text.config(state="disabled")

    def show_trait_screen(self, idx: int):
        self.clear_page()
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

        frm = ttk.Frame(self.page_frame, padding=12)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text=f"Trait {idx + 1} of {len(self.active_traits)}", font=("TkDefaultFont", self.settings["font_size"] + 1, "bold")).pack(anchor="w")
        ttk.Label(frm, text=trait["name"], font=("TkDefaultFont", self.settings["font_size"] + 4, "bold")).pack(anchor="w", pady=4)
        ttk.Label(frm, text=f"Priority: {trait['priority']} | Weight: x{trait['weight']}").pack(anchor="w")
        ttk.Label(frm, text=f"Primary Question: {trait['primary_question']}", wraplength=1050).pack(anchor="w", pady=6)

        ladder_frame = ttk.LabelFrame(frm, text="Scoring descriptors (1-5)")
        ladder_frame.pack(fill="x", pady=4)
        for n in [5, 4, 3, 2, 1]:
            ttk.Label(ladder_frame, text=f"{n}: {trait['descriptors'][str(n)]}", wraplength=1030).pack(anchor="w")

        sample_frame = ttk.LabelFrame(frm, text="Sample answers (display only)")
        sample_frame.pack(fill="x", pady=4)
        for n in [5, 4, 3, 2, 1]:
            ttk.Label(sample_frame, text=f"{n}: {trait['sample_answers'][str(n)]}", wraplength=1030).pack(anchor="w")


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
        q_text = tk.Text(notes_frame, height=4, wrap="word", font=("TkDefaultFont", self.settings["font_size"]))
        q_text.pack(fill="x", pady=2)
        q_text.insert("1.0", state.get("question_notes", ""))

        ttk.Label(notes_frame, text="Trait-level notes").pack(anchor="w")
        t_text = tk.Text(notes_frame, height=4, wrap="word", font=("TkDefaultFont", self.settings["font_size"]))
        t_text.pack(fill="x", pady=2)
        t_text.insert("1.0", state.get("trait_notes", ""))

        ttk.Label(notes_frame, text="Verbatim quote / notes for disqualifier logging").pack(anchor="w")
        v_text = tk.Text(notes_frame, height=4, wrap="word", font=("TkDefaultFont", self.settings["font_size"]))
        v_text.pack(fill="x", pady=2)
        v_text.insert("1.0", state.get("verbatim_notes", ""))

        ttk.Checkbutton(
            notes_frame,
            text="Absolute disqualifier observed in this trait",
            variable=dq_var,
        ).pack(anchor="w", pady=4)

        ttk.Button(notes_frame, text="View Absolute Disqualifiers Reference", command=self.show_disqualifier_reference).pack(anchor="w")

        self._render_signal_examples(frm, tid)

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

        self.set_footer_actions(
            left_actions=[
                ("Back", go_back),
                ("Next", go_next),
                ("Save Draft", save_draft),
            ],
            right_actions=[
                ("Finalize", finalize),
                ("Exit", self.destroy),
            ],
        )

    def validate_before_finalize(self):
        if not self.state.candidate_name.strip():
            raise ValueError("Candidate Name is required.")
        if not is_valid_date_yyyy_mm_dd(self.state.interview_date.strip()):
            raise ValueError("Interview Date must be valid YYYY-MM-DD.")
        if not self.state.school.strip():
            raise ValueError("School selection is required.")
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
