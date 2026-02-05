# Structured Behavioral Interview Desktop App

Offline Tkinter desktop tool for running the structured preschool teacher Zoom interview rubric, capturing scores and notes, applying overrides, and generating one Word report per candidate.

## Requirements

- Python 3.11+
- `python-docx`

Install dependency:

```bash
pip install python-docx
```

## Run

From the folder containing `app.py`:

```bash
python app.py
```

## Key files

- `app.py` – Tkinter desktop app
- `rubric.json` – rubric content + scoring config (tracks, traits, weights, thresholds, overrides)
- `disqualifier_signals.json` – disqualifier-signal examples + probe prompts shown on question screens
- `sample_draft.json` – example saved draft format

## Interview workflow

1. **Start screen**: New Interview, Open Draft, Settings, Exit.
2. **Candidate intake**:
   - Candidate Name (required)
   - Interview Date (required, YYYY-MM-DD, defaults to today)
   - School/Location (required; preset options + add custom school)
   - Track (required: Infant/Toddler or Preschool)
3. **Trait screens** (one trait/question at a time):
   - Scoring ladder + sample answers
   - Raw score (1–5)
   - Question notes, trait notes, verbatim notes
   - Absolute disqualifier checkbox (verbatim notes required if checked)
   - Disqualifier-signal examples and probes shown below scoring/notes
4. **Save Draft** any time and resume later.
5. **Finalize** to generate one `.docx` report.

## Output

Default folders (auto-created under app directory):

- Drafts: `./interviews/drafts`
- Final reports: `./interviews/final`

Report filename format:

```text
{YYYY-MM-DD} - {School} - {CandidateName} - Interview.docx
```

(Names are sanitized for filesystem safety.)

## Notes

- Offline-only runtime behavior.
- GUI is scrollable for long interview screens.
- Text size can be adjusted during interviews via the toolbar (`A-` / `A+`).
