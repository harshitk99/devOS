# DevOS Research Paper - LaTeX/Overleaf Template

## Quick Start

### Option 1: Overleaf (Recommended)
1. Go to [overleaf.com](https://overleaf.com) → New Project → **Upload Project**
2. Zip this entire `paper-template` folder and upload
3. Click **Recompile** — PDF generates automatically

### Option 2: Local Compilation
```bash
cd paper-template
pdflatex main.tex
bibtex main
pdflatex main.tex
pdflatex main.tex
```
Output: `main.pdf`

## Template Structure

| File | Purpose |
|------|---------|
| `main.tex` | Complete paper with all sections (Abstract → Appendix → References) |
| `references.bib` | Bibliography in BibTeX format (35+ citations, organized by topic) |
| `figures/architecture.tex` | Standalone TikZ architecture diagram source |
| `README.md` | This file |

## Paper Sections

1. **Abstract** — ~200 words, positions against LLM-based APR and SWE agents, covers problem, solution, key results
2. **Keywords** — IEEE-style comma-separated, includes "LLM Safety Guardrails"
3. **Introduction** — Motivation, gap analysis against 2024–2025 agent landscape, contributions (6), roadmap
4. **Literature Review** — Comparison table (6 columns) + 6 subsections:
   - Automated Program Repair (APR) — classical techniques
   - LLM-Based APR — deep learning and prompt-based repair, surveys
   - Autonomous SWE Agents — SWE-bench, SWE-agent, AutoCodeRover, Devin
   - AI Coding Assistants — Copilot, Cursor, Codeium, Claude Code + security risks
   - Developer Environment Platforms — Gitpod, Codespaces, Dev Containers + config drift
   - Self-Healing Infrastructure — MAPE-K, Kubernetes operators
5. **Research Methodology** — Design principles, **threat model** (new), architecture figure, 7 subsections + Algorithm pseudocode (new) + implementation details
6. **Results and Discussion** — 9 scenario results table, qualitative analysis per scenario, **comparison with autonomous agents** (new), performance, test coverage, limitations (7 items)
7. **Conclusion** — Summary + future work (8 items)
8. **Appendix** — Action types table, Finding types table, **MAPE-K mapping table** (new)
9. **References** — 35+ citations in `references.bib`, organized by topic area

## Key Improvements (v2)

- **Expanded Literature Review**: 6 subsections (was 4) with dedicated coverage of LLM-based APR and autonomous SWE agents
- **Expanded Comparison Table**: Added "LLM SWE Agents" column with footnotes
- **Threat Model Section**: New section detailing security design decisions against documented attack vectors
- **Algorithm Pseudocode**: Formal Algorithm 1 for the deterministic planning process
- **13 New Citations**: SWE-bench (ICLR'24), SWE-agent (NeurIPS'24), AutoCodeRover (ISSTA'24), Devin, APR surveys (2023/2024/2025), LLM code security, prompt injection, configuration drift
- **Agent Comparison Discussion**: New subsection in Results explaining complementary relationship with SWE agents
- **MAPE-K Mapping**: New appendix table mapping DevOS to the autonomic computing reference model
- **Compact Lists**: Uses `enumitem` package for tighter formatting within page limits

## Customization Checklist

- [ ] Update author affiliations/emails in `main.tex` (lines ~99–105)
- [ ] Add your GitHub repo URL in `references.bib` (`devos-repo` entry)
- [ ] Update arXiv IDs in `llm-apr-survey-2025` and `swebench-verified` citations once published
- [ ] Update scenario details in Table 2 if your results differ
- [ ] Add/remove citations in `references.bib` as needed
- [ ] Adjust page count: IEEE conference = 6–8 pages (current ~8–9 with appendix)

## Figures

The architecture diagram is embedded directly as TikZ in `main.tex` (no external PDF needed). It renders automatically during compilation.

Alternatively, create your own diagram using:
- **Draw.io** (diagrams.net) → Export as PDF
- **Excalidraw** → Export as SVG → Convert to PDF
- **Mermaid** (in Markdown) → `mmdc -i arch.mmd -o architecture.pdf`
- **TikZ** (directly in LaTeX) — see `figures/architecture.tex` for a standalone version

## Common Issues

| Issue | Fix |
|-------|-----|
| `File 'architecture.pdf' not found` | Not needed — diagram is embedded as TikZ |
| Citation undefined | Run `bibtex main` between `pdflatex` runs |
| Overfull hbox | Check table widths; use `tabular*` or `adjustbox` |
| Page limit exceeded | Move appendix tables to supplementary; shorten discussions |
| `enumitem` not found | Install via `tlmgr install enumitem` or use Overleaf |

## Submission Venues (B.Tech Minor Project)

| Venue | Format | Page Limit | Deadline (Typical) |
|-------|--------|------------|-------------------|
| IEEE ICSC / ICCI*CC | IEEEtran | 6–8 pages | Varies |
| ACM COMPSAC | ACM sigconf | 8–10 pages | ~Feb/May |
| IEEEXtreme / Student Conf | IEEEtran | 4–6 pages | Varies |
| College Journal / Symposium | Custom | 6–10 pages | Ask advisor |

---

**Tip:** For your B.Tech submission, confirm the required format (IEEE vs ACM vs college template) with your supervisor. This template uses `IEEEtran` — change `\documentclass[conference]{IEEEtran}` to `\documentclass[sigconf]{acmart}` for ACM format.