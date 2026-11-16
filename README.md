# Atelier

A team of AI agents that works like me: it builds, markets and maintains websites for my clients with my own method and taste — while I stay human on the loop.

Small tasks: the agents do them and tell me. Important tasks: they prepare everything and ask me before publishing.

## What works today

**The control panel.** A small web app that runs on my own computer. It shows my clients, what the agents have done, what is waiting for my decision, and how much the agents have spent.

**The Web agent, step 1 of my method: look at what the client already has.** One click starts an in-depth analysis of a client's website:

- it reads the whole site (up to 40 pages) and checks facts instead of guessing: broken links, legal pages, titles, descriptions, headings, images without alt text, sitemap, robots, the 404 page and https
- it takes screenshots on desktop and on mobile, and taps the phone menu to check that it really opens
- it measures the homepage with Google PageSpeed: speed, accessibility, good practices and SEO
- it asks Claude for a report: eight areas with a score from 0 to 100, what to change first, and questions for me

The report waits in the panel until I approve or reject it.

**The Web agent, step 2 of my method: look for inspiration (first part).** To be inspired, never to copy. One click starts a search for a client:

- it searches the web for websites that design galleries have recognised (Awwwards, CSS Design Awards, FWA and others like them)
- it checks what it found instead of trusting it: an award only counts as checked when the page that names it is really among the search results
- it opens each website like a visitor, on desktop and on mobile, and keeps pictures and the typefaces it uses
- it respects `robots.txt`: a website that asks programs to stay out is not opened

The candidates wait in the panel. I mark the ones that inspire me and what I like about each (design, kind of page, animations, effects), and I can add links of my own, which is free.

**The CRM, first part.** My own place for the clinics I want as clients, so that I need no other tool:

- "Hoy" opens the day: the follow-ups I let slip, today's, the conversations with no next step, and the next clinics to write to
- the list of clinics, with search and filters by neighbourhood, kind, stage and batch
- one card per clinic: its details, the people in it (who decides and how they like to be reached), its star treatments, its Google reviews and how well it fits me
- a history of every contact. Noting a contact moves the clinic along the path by itself: to contact, contacted, replied, call booked, call done, proposal sent, client
- always a next step with a day. After a diagnosis call the written proposal is set for the next day, as in my method
- "do not contact", and a button that erases a clinic with its people and its history when someone asks me to
- it reads a clinic's own website, for free: it checks that the website is really theirs (its phone or its street must be on it) and collects its social profiles. When I do not know the website, it tries the addresses the clinic's name suggests and keeps one only if it passes that check
- a quick X-ray of each clinic, for free and in about half a minute: where it loses patients, in the order a patient walks. 1) They find it: Google reviews, Instagram, ads, something that measures visits. 2) They choose it: its website, with Google's speed test on a phone. 3) They write and book: WhatsApp, online booking, a chat, a phone that can be tapped. Only checked facts; what cannot be seen from outside becomes a question for the diagnosis call. The biggest hole comes first, in one plain sentence: it is the topic of my video audit. One button for a clinic, one for all of them, and a filter by hole in the list

Atelier never sends a message. The links only open WhatsApp, the phone or the mail on my computer, and I write.

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

The panel is at http://127.0.0.1:8000 and the CRM at http://127.0.0.1:8000/crm. To check that screenshots work: `python capture.py --check`.

The CRM starts empty. To try it with invented clinics, copy `sample_data/clinics-demo.json` into a folder called `private` and open the list of clinics: it offers to bring them in.

## What it costs

The agent uses the Claude API, which costs money: about 0.16 USD per in-depth analysis, and an estimated 0.25 to 0.40 USD per search for inspiration (the tokens plus 0.01 USD for each web search). Every call is logged, and the agent stops by itself at a fixed budget of 5 USD, set in `web_agent.py`. Google PageSpeed is free. Opening a website to take pictures is free. The CRM, reading a clinic's website and the quick X-ray do not use the Claude API: they are free.

## What stays private

The database, the reports and candidates with their screenshots and the `.env` file with the keys stay on my computer: git ignores them. No real client data goes in this repository.

The clinics and the people of the CRM are real, so they live only in the local database. A list of clinics waiting to come in sits in `private/`, which git ignores too, and the file is deleted once they are in.

## The files

- `main.py` — the API and the pages of the panel
- `database.py` — SQLite storage: clients, tasks, reports, API spending and the CRM
- `crm.py` — the CRM: clinics, people, the history of contacts, next steps and what to do today
- `clinic_web.py` — finds and reads a clinic's own website: checks that it is theirs and collects its social profiles
- `xray.py` — the quick X-ray of a clinic: its rules, and the measuring (its website and Google PageSpeed)
- `web_agent.py` — the Web agent, step 1: gathers everything, asks Claude and saves the report
- `inspiration.py` — the Web agent, step 2: searches for websites that can inspire and looks at each one
- `site_reader.py` — reads the website and checks the facts
- `capture.py` — screenshots on desktop and on mobile, with Playwright
- `pagespeed.py` — Google PageSpeed scores
- `static/` — the panel (`index.html`), the report page (`report.html`), the inspiration page (`inspiration.html`) and the CRM (`crm.html`, `clinic.html`, with their shared `crm.css` and `crm.js`)
- `sample_data/` — invented clinics for trying the CRM

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