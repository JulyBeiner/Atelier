# Atelier

A team of AI agents that works like me: it builds, markets and maintains websites for my clients with my own method and taste — while I stay human on the loop.

Small tasks: the agents do them and tell me. Important tasks: they prepare everything and ask me before publishing.

## What works today

**The control panel.** A small web app that runs on my own computer. It shows my clients, what the agents have done, what is waiting for my decision, and how much the agents have spent.

**The Web agent, step 1 of my method: look at what the client already has.** One click starts an in-depth analysis of a client's website:

- it reads the whole site (up to 40 pages) and checks facts instead of guessing: broken links, legal pages, titles, descriptions, headings, images without alt text, sitemap, robots, the 404 page and https
- it takes screenshots on desktop and on mobile
- it measures the homepage with Google PageSpeed: speed, accessibility, good practices and SEO
- it asks Claude for a report: eight areas with a score from 0 to 100, what to change first, and questions for me

The report waits in the panel until I approve or reject it.

## Run it

Built with Python 3.14 on Windows. The screenshots use the Edge or Chrome already on the computer.

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Open `.env` and paste two keys: `ANTHROPIC_API_KEY` (the agent needs it) and `PAGESPEED_API_KEY` (free and optional, but without it Google often refuses to measure). Then start the panel:

```
fastapi dev main.py
```

The panel is at http://127.0.0.1:8000. To check that screenshots work: `python capture.py --check`.

## What it costs

The agent uses the Claude API, which costs money: about 0.16 USD per in-depth analysis. Every call is logged, and the agent stops by itself at a fixed budget of 5 USD, set in `web_agent.py`. Google PageSpeed is free.

## What stays private

The database, the reports with their screenshots and the `.env` file with the keys stay on my computer: git ignores them. No real client data goes in this repository.

## The files

- `main.py` — the API and the pages of the panel
- `database.py` — SQLite storage: clients, tasks, reports and API spending
- `web_agent.py` — the Web agent: gathers everything, asks Claude and saves the report
- `site_reader.py` — reads the website and checks the facts
- `capture.py` — screenshots on desktop and on mobile, with Playwright
- `pagespeed.py` — Google PageSpeed scores
- `static/` — the panel (`index.html`) and the report page (`report.html`)

## Departments
1. **Brand** — branding, logo, colors, typography
2. **Web** — client research → Awwwards-style inspiration (never copying) → build with Claude → review
3. **Digital marketing**
4. **Automations** — create and maintain workflows (e.g. n8n)
5. **Maintenance** — keep sites working, updated and error-free
6. **Detective** — finds what I still do by hand and proposes automating it

## Roadmap (end-to-end AI)
1. Full-stack software — the control panel
2. Cloud & DevOps
3. Data engineering
4. Analytics
5. Data science
6. ML & deep learning — a model that learns my taste (NumPy first)
7. Vision, language & voice
8. MLOps
9. AI engineering — RAG & evals
10. Agentic AI — all departments working together
11. Interpretability research

Built by Jul Beiner