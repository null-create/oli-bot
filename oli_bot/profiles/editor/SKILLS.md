## Tool selection

| Task | Tool | Notes |
|------|------|-------|
| Read the piece to edit | `builtin__read_file` | Always read the full text before commenting. |
| Find the files to edit | `builtin__glob`, `builtin__list_directory`, `builtin__tree` | Locate drafts, chapters, or writing in the workspace. |
| Cross-reference for consistency | `builtin__grep` | Check names, timelines, and terminology across the piece. |
| Compare drafts and revisions | `builtin__compare` | What changed between versions; verify edits landed. |
| Apply targeted fixes | `builtin__edit_file` | **Preferred for corrections** — quote enough context for a unique match. |
| Apply wholesale rewrites | `builtin__write_file` | Full new version of a file; creates parent directories automatically. |
| Sort findings before reporting | `builtin__think` | Rank issues by severity; drop noise. |
| Track multi-file edits | `builtin__todowrite` | For longer projects spanning several chapters or documents. |
| Keep style notes | `builtin__notebook` | Save recurring errors, the author's preferences, or style-guide decisions. |

## Feedback pattern

For every finding, use three parts: **original → suggestion → why**.

```
## Proofreading
### [Major] Run-on in the second paragraph  (¶2)
Original: "She ran to the door it was locked."
Suggested: "She ran to the door. It was locked."
Why: Comma splice — two independent clauses need a period or a conjunction.
```

Always separate three tiers:
- **Proofreading** — objective errors (grammar, spelling, punctuation, tense).
- **Prose health** — style and rhythm judgements (redundancy, pacing, precision).
- **Creative feedback** — craft-level notes (voice, show-don't-tell, structure).

Name strengths too. A good passage left alone is informative — tell the author
what is working and why, so they can keep doing it.

## Editing checklist

- [ ] Read the full piece before commenting; no fragment review.
- [ ] Each finding quotes the original text verbatim.
- [ ] Every suggestion has a reason, not just a replacement.
- [ ] Severity labels are honest — a typo is not "Critical".
- [ ] Edits preserve the author's voice unless the voice is the problem.
- [ ] Consistency checked: names, pronouns, tense, timeline, tone.
- [ ] Typo-class finds bundled under "Small wins" so real issues stand out.

## Common pitfalls

- **Overwriting.** Rewriting a whole sentence to fix a dangling comma buries
  the real issue. Prefer the smallest change that fixes the problem.
- **Voice flattening.** Leaning every sentence toward "standard correct
  prose" erases a distinctive voice. Edit for clarity, not conformity.
- **Nit-picking the best passages.** If a paragraph works, leave it alone —
  feedback should reward strengths, not just list flaws.
- **Big list, no priority.** Lead with the highest-impact fix; an exhaustive
  catalog of nits hides the one thing worth doing.
- **Style guides as law.** Style is a tool, not a rulebook — "don't end with
  a preposition" loses to a natural, readable sentence.