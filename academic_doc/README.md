# FedWatcher academic documentation

This directory holds the draft of the academic PDF required by the
"Programming in Finance II 2026" big-projects brief. It contains only
project-authored documentation sources and generated output. Course
briefs, slides, and other files from `FEDWatcher_Hide` remain local-only
and must not be committed to this public repository.

## Structure

```
academic_doc/
  main.tex                  # master file
  sections/
    00_project_plan.tex
    01_diary.tex            # seeded from git log; edit as needed
    02_methods.tex
    03_results.tex          # contains TODO markers
    04_lessons.tex
    05_ai_acknowledgement.tex
  bib/                      # bibtex if needed
  raw/
    commit_log.txt          # raw commit log dump (seed material)
  Makefile                  # latexmk -pdf
```

## Build

```bash
make          # one-shot build -> main.pdf
make watch    # latexmk live-rebuild on save
make open     # open main.pdf in default viewer (macOS)
make wordcount
make clean
```

Requires a TeX Live install with `latexmk`. On macOS the easiest path is
MacTeX (`brew install --cask mactex-no-gui`).

## Page budget

The brief asks for **5--8 pages of pure text**. The current scaffold is
already in that ballpark; if you add figures or tables they should sit
alongside the text, not replace it.

## TODOs

Grep for `%% TODO` in `sections/03_results.tex` to find the numeric
backtest results and the divergence case study that still need real
content once the nowcast is finalized.
