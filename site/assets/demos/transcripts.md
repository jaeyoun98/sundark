# Demonstration transcripts

These silent recordings show the real Sundark desktop web app using the public paper [Attention Is All You Need](https://arxiv.org/abs/1706.03762). Remotion adds a paper-like stage, an eased camera, and paired close-up crops of the same genuine capture. The close-ups are editorial framing rather than new app windows. They are not physical mobile-device recordings. Use the player's full-screen control to inspect the paper and equations.

## Translation — 18 seconds

- 0:00: The paper and its Korean translation are open side by side.
- 0:02.5: Selecting the sentence proposing the Transformer highlights its matching source sentence.
- 0:06: Two enlarged crops pair the actual selected English sentence with its Korean translation. A nonverbal connector emphasizes the relationship.
- 0:13.8: The close-up crops settle back into the wide app view.

The translation was genuinely generated through the app's Claude Code backend using `sonnet` before filming. The clip demonstrates sentence navigation with prepared results; it makes no claim about generation speed. The selected real browser frame is held for reading during the close-up scene and return. No application content is replaced.

## Paper-aware chat — 22.5 seconds

- 0:00: The question asks why scaled dot-product attention divides by the square root of d_k, with section 3.2.1 as the source.
- 0:02: The question is submitted to the real app.
- 0:03.5: The generated answer is shown beside the paper, with captions inviting the reader to find answers in its explanations and equations.
- 0:06.5: The actual answer defines d_k as the dimension of query and key vectors and shows scaling before softmax.
- 0:07.4: Paired close-up crops show the paper's equation (1) beside the unchanged generated explanation. Reading continues through the explanation that large dot products push softmax into regions with very small gradients; scaling counters that effect.
- 0:18.1: The view returns to the wide app, with the attention diagram, section 3.2.1, equation (1), and footnote 4 beside the answer.

The answer was genuinely generated using the app's `sonnet` chat task. A waiting interval was removed at 3.5 seconds and the PDF was repositioned between shots. These production edits are documented here and in provenance.json. It is not a real-time latency demonstration. The later answer-reading capture uses the app's saved chat history without changing the text.

## Research Anatomy — 18 seconds

- 0:00: Research Anatomy shows the main claim that the Transformer relies on attention without recurrence or convolutions.
- 0:02.5: A real pointer selects the verbatim source quotation marked as verified by the app.
- 0:06: Paired close-up crops show the Main Claim beside its matching abstract sentence, highlighted by the app.
- 0:13.8: The crops settle back into the wide app, preserving the claim and its source context.

The app generated the actual Anatomy using the supported per-task model selection `claude-haiku-5-5`. Default preparation attempts timed out. In the isolated preparation runtime, supported Claude Code environment settings reduced effort to `low` and capped output at 8192 tokens. The app's existing JSON-repair fallback produced the complete parseable result, and its ordinary source-quote verification marked the shown quote as verified. The app source, prompt, security settings, and 300-second timeout were unchanged. Generation happened before filming; this is a navigation demonstration, not a generation-speed claim.

## Caption choices

All editorial captions are separate WebVTT tracks in Korean (`ko`) and English (`en`). Use the player's caption menu to choose a language or turn them off. No editorial captions are permanently embedded in the video image. The real app's Korean text is part of the captured UI.
