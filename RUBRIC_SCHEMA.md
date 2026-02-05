# rubric.json Human-Readable Schema

`rubric.json` drives all interview screens and scoring logic. The Python app does not hardcode trait content.

## Top-level fields

- `metadata` (object)
  - `title` (string)
  - `subtitle` (string)
  - `notes` (string[])
  - `version` (string)
- `scoring` (object)
  - `raw_score_range` (number[2])
  - `one_score_per_trait` (boolean)
  - `primary_question_only` (boolean)
  - `weights` (object): maps priority names to multiplier.
    - `Critical`, `High`, `Medium` (numbers)
  - `weighted_formula` (string)
  - `automatic_overrides` (string[])
  - `conflict_resolution` (string[])
- `tracks` (object)
  - key is track id, e.g. `infant_toddler` or `preschool`
  - value includes:
    - `label` (string)
    - `max_weighted_total` (number)
    - `thresholds` (object)
      - `hire_percent_min` (number)
      - `borderline_percent_min` (number)
      - `borderline_percent_max` (number)
      - `no_hire_percent_below` (number)
      - `hire_requires` (string[])
      - `borderline_requires` (string[])
- `absolute_disqualifiers` (string[])
- `interviewer_guidance` (object)
  - `what_to_evaluate` (string[])
  - `scoring_principles` (string[])
  - `probe_when` (string[])
  - `probe_prompt` (string)
- `traits` (array of trait objects)
  - each trait object has:
    - `id` (string, unique)
    - `name` (string)
    - `priority` (string; `Critical|High|Medium`)
    - `weight` (number)
    - `applicable_tracks` (string[]; includes `all` and/or track ids)
    - `primary_question` (string)
    - `descriptors` (object with keys `"1".."5"`)
    - `sample_answers` (object with keys `"1".."5"`)
    - `score_1_auto_no_hire` (boolean)
- `final_record_fields` (string[])
- `canonical_text` (string): full original rubric text for auditability.

## Runtime usage by app.py

- Candidate selects a track.
- Traits are filtered by `applicable_tracks` (`all` plus selected track).
- One screen per trait is rendered from trait JSON.
- Scoring reads trait `weight` and `priority`, then applies thresholds from selected track.
- DOCX output lists all selected trait results with notes and override summary.

## Additional file: disqualifier_signals.json

- Contains per-question disqualifier-signal example statements and probe prompts.
- App maps entries by `trait_id` and renders them on each trait screen for interviewer reference.
- Includes fields: `question_id`, `trait_id`, `trait_name`, `track`, and `disqualifier_signals[]` with `disqualifier_type`, `auto_disqualify_if_confirmed`, `examples[]`, `probe_to_confirm`.
