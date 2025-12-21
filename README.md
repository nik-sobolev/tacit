# Tacit - Your Personal Work Twin

**Keep your work moving when you're not in the room.**

Tacit is your digital twin that captures your decisions, context, and plans so work can keep moving forward even when you're unavailable. It turns your tacit knowledge into clear next steps, updates, and narratives your team can use to unblock work and make confident decisions.

## Features

### ✅ MVP Complete (v0.1)

**Core Capabilities:**
- **Context Capture** - Log 6 types: decisions, meeting notes, project context, strategies, insights, plans
- **Document Intelligence** - Upload and search PDFs, DOCX, TXT, MD files with semantic search
- **Executive Coaching** - Get coached while your twin learns your thinking patterns
- **Unified Chat** - Ask questions, get coached, query knowledge - all in one interface
- **Hybrid Search** - Searches both contexts and documents simultaneously with relevance scoring

**Knowledge Management:**
- **Context Library** - View, edit, and delete saved contexts with visual organization
- **Document Library** - Browse, manage, and delete uploaded documents
- **Source Citations** - Detailed references with page numbers, dates, and relevance scores

**Technical:**
- **Persistent Storage** - SQLite database for metadata, ChromaDB for vector search
- **Markdown Support** - Rich formatted responses with proper rendering
- **Smart Loading States** - Spinners and retry functionality for better UX
- **Error Handling** - Network timeouts, retry buttons, graceful failures

### Coming Soon (Phase 2)
- Calendar integration (auto-capture meeting context)
- Email integration (communication context)
- Team access (let your team query your twin)
- Auto-generated status updates
- Slack integration

## Quick Start

### 1. Setup

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env
```

**Edit `.env` file and configure:**
1. `ANTHROPIC_API_KEY` - Your Claude API key (required)
2. `USER_NAME` - Your name (e.g., "Sarah Chen")
3. `USER_ROLE` - Your role (e.g., "VP Engineering")
4. `USER_ORGANIZATION` - Your company (e.g., "Acme Corp")

This personalizes Tacit to speak as YOUR digital twin.

### 2. Run

```bash
cd backend
python3 -m app.main
```

Or with uvicorn:
```bash
cd backend
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### 3. Access

Open http://127.0.0.1:8000 in your browser

## Architecture

- **Backend:** FastAPI + SQLite + ChromaDB
- **Frontend:** Modern HTML/CSS/JS
- **AI:** Claude (Anthropic) + ChromaDB embeddings
- **Storage:** Local (SQLite for metadata, ChromaDB for vectors)

## Project Structure

```
tacit/
├── backend/          # FastAPI application
│   ├── app/          # Application code
│   └── data/         # Local data storage
├── frontend/         # Web interface
└── docs/            # Documentation
```

## Development Timeline

✅ **Day 1:** Foundation + Context Capture + Coaching (Complete)
✅ **Day 2:** Document Upload + Semantic Search + Persistence (Complete)
✅ **Day 3:** Integration + Polish + Testing + Enhanced UX (Complete)

**Status:** MVP v0.1 COMPLETE

See [TACIT_3DAY_PLAN.md](TACIT_3DAY_PLAN.md) for detailed plan.
See [DAY1_COMPLETE.md](DAY1_COMPLETE.md) for Day 1 summary.

## Usage Guide

### Capturing Context
1. Use the **Quick Capture** tab in the right sidebar
2. Select context type (decision, meeting note, etc.)
3. Add title, content, and optional tags
4. Click "Save Context" - it's now searchable!

### Uploading Documents
1. Switch to **Upload** tab
2. Select PDF, DOCX, TXT, or MD file (max 50MB)
3. Upload - Tacit will chunk and index it automatically
4. Ask questions about your documents in chat

### Managing Knowledge
- Click 📝 in header to view/edit/delete contexts
- Click 📄 in header to browse/delete documents
- Click 📊 in header to see stats

### Chatting with Your Twin
- **Ask questions:** "What did I decide about the hiring process?"
- **Get coached:** "Help me think through this leadership challenge"
- **Query documents:** "What does the 10-K say about revenue?"

Your twin searches contexts and documents, cites sources, and responds in your voice!

## License

Proprietary - Internal Use Only

---

**Built with** ❤️ **for executives who need to multiply their impact**
