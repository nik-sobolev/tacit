# Tacit - 3-Day MVP Implementation Plan

**Project:** Tacit - Your Personal Work Twin
**URL:** trytacit.app
**Timeline:** 3 Days
**Mode:** Single User MVP

---

## Vision

Tacit captures your tacit knowledge (decisions, context, plans, documents) and acts as your digital twin to:
1. Coach you (executive coaching mode)
2. Answer questions about your knowledge
3. Help team members understand your thinking when you're unavailable

---

## MVP Scope (3 Days)

### Core Features
1. **Manual Context Capture** - Log decisions, plans, meeting notes, context
2. **Document Upload & Search** - Upload PDFs/docs, semantic search over content
3. **Executive Coaching Mode** - Coach Karen's proven coaching engine
4. **Unified Chat Interface** - Ask questions, get coached, query your knowledge

### Tech Stack (Optimized for Speed)
- **Backend:** FastAPI (port from Coach Karen)
- **Frontend:** Enhanced HTML/JS/CSS (Coach Karen style, refined)
- **Database:** SQLite (structured data) + ChromaDB (vector search)
- **Auth:** None (single user, localhost)
- **Deployment:** Local first, cloud-ready

---

## 3-Day Timeline

### **Day 1: Foundation + Context Capture + Coaching**
**Goal:** Working chat interface with coaching + manual context logging

**Tasks:**
1. ✅ Create directory structure
2. Set up FastAPI backend with ChromaDB
3. Port Coach Karen coaching engine
4. Build context capture form (decisions, notes, plans)
5. Basic chat UI with coaching mode
6. Store contexts in vector DB

**Deliverable:** Can chat with twin, log context, get coached

---

### **Day 2: Document Intelligence**
**Goal:** Upload documents, search semantically, query in chat

**Tasks:**
1. Document upload API (PDF, DOCX, TXT, MD)
2. Text extraction (PyPDF2, python-docx)
3. ChromaDB embedding + indexing
4. Semantic search endpoint
5. Integrate doc search into chat
6. Show sources in responses

**Deliverable:** Upload documents, ask questions, twin answers from docs + context

---

### **Day 3: Integration + Polish + Testing**
**Goal:** Unified experience, polished UI, tested

**Tasks:**
1. Unified interface (coaching + context + docs in one chat)
2. Context management UI (view/edit logged contexts)
3. Document library UI (view/delete uploaded docs)
4. Response quality improvements
5. UI polish (spacing, colors, UX)
6. End-to-end testing
7. Documentation

**Deliverable:** Shippable MVP - Tacit Digital Twin v0.1

---

## Directory Structure

```
tacit/
├── TACIT_3DAY_PLAN.md          # This file
├── README.md                    # Project overview
├── backend/
│   ├── app/
│   │   ├── main.py             # FastAPI entry point
│   │   ├── api/
│   │   │   ├── chat.py         # Chat endpoints
│   │   │   ├── context.py      # Context capture endpoints
│   │   │   ├── documents.py    # Document upload/search
│   │   │   └── coaching.py     # Coaching endpoints
│   │   ├── core/
│   │   │   ├── engine.py       # Twin engine (from Coach Karen)
│   │   │   ├── config.py       # Configuration
│   │   │   └── prompts.py      # System prompts
│   │   ├── services/
│   │   │   ├── coaching_service.py
│   │   │   ├── context_service.py
│   │   │   ├── document_service.py
│   │   │   └── vector_service.py  # ChromaDB wrapper
│   │   ├── models/
│   │   │   ├── context.py      # Context data models
│   │   │   ├── document.py     # Document models
│   │   │   └── chat.py         # Chat models
│   │   └── db/
│   │       ├── sqlite.py       # SQLite setup
│   │       └── chroma.py       # ChromaDB setup
│   ├── data/                   # Local data storage
│   │   ├── uploads/            # Uploaded documents
│   │   ├── chroma/             # Vector DB
│   │   └── tacit.db            # SQLite database
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── static/
│   │   ├── index.html          # Main interface
│   │   ├── styles.css          # Tacit styling
│   │   └── app.js              # Frontend logic
│   └── assets/
│       └── logo.svg
└── docs/
    ├── ARCHITECTURE.md
    └── API.md
```

---

## Feature Details

### 1. Manual Context Capture
**What:** Quick form to log tacit knowledge

**Types:**
- **Decision:** What you decided and why
- **Meeting Note:** Key points, action items, context
- **Project Context:** Background, status, blockers
- **Strategy:** Plans, reasoning, tradeoffs
- **Insight:** Learnings, patterns, observations

**Fields:**
- Title
- Type (dropdown)
- Content (rich text)
- Tags (comma-separated)
- Date
- Related to (project/person)

**Storage:** ChromaDB (embedded for search) + SQLite (metadata)

---

### 2. Document Upload & Search
**Supported:** PDF, DOCX, TXT, MD, Google Docs export

**Process:**
1. Upload file → FastAPI endpoint
2. Extract text (PyPDF2, python-docx, pandoc)
3. Chunk text (500 tokens, 50 overlap)
4. Embed chunks → ChromaDB
5. Store metadata → SQLite

**Search:**
- Semantic search via ChromaDB
- Return top 5 relevant chunks
- Show source document + page

---

### 3. Executive Coaching Mode
**Port from Coach Karen:**
- Coaching engine
- Conversation manager
- Skill modules (active listening, powerful questioning)
- Session management

**Adaptation:**
- Use captured context to inform coaching
- Reference past decisions in coaching
- Help articulate tacit knowledge

---

### 4. Unified Chat Interface
**Single chat that handles:**
- General questions about your knowledge
- Coaching conversations
- Document queries
- Context retrieval

**Intelligence:**
- Check context DB first
- Check document DB
- Use coaching mode if personal development question
- Combine sources for comprehensive answers

**UI Features:**
- Message history
- Source citations
- Mode indicator (coaching/query/mixed)
- Context suggestions

---

## System Prompts

### Main Twin Prompt
```
You are Tacit, a digital twin for [User Name].

You have access to:
1. Manual contexts logged by the user (decisions, meeting notes, project context)
2. Uploaded documents (presentations, notes, PDFs)
3. Executive coaching capabilities (Coach Karen style)

Your role:
- Answer questions based on the user's knowledge and documented thinking
- Provide executive coaching when needed
- Help articulate tacit knowledge into clear narratives
- Assist in decision-making by referencing past patterns
- Generate updates and summaries from logged context

Always:
- Cite sources when referencing specific documents or contexts
- Distinguish between what you know from user's data vs general knowledge
- Be direct, actionable, and concise
- Use the user's voice and decision-making patterns
```

---

## Success Metrics (MVP)

**Day 1:**
- ✅ Can log 3+ contexts
- ✅ Can have coaching conversation
- ✅ Twin references logged contexts

**Day 2:**
- ✅ Can upload 3+ documents
- ✅ Can search documents semantically
- ✅ Twin answers from documents

**Day 3:**
- ✅ All features work together
- ✅ UI is polished and intuitive
- ✅ No critical bugs
- ✅ Can demo to team

---

## Post-MVP (Phase 2)

**Week 2:**
- Calendar integration (Google Calendar API)
- Auto-capture meeting summaries
- Team sharing (simple auth, share link)

**Week 3:**
- Email integration (Gmail API)
- Auto-generate status updates
- Slack bot integration

**Week 4:**
- Multi-user support
- Organization structure
- Advanced analytics

---

## Technical Decisions

### Why SQLite + ChromaDB (not PostgreSQL)?
- **Speed:** No server setup, instant start
- **Simplicity:** Single file database
- **Portability:** Easy to backup/move
- **Sufficient:** Handles 10k+ docs easily
- **Upgrade path:** Can migrate to Postgres later

### Why Enhanced HTML/CSS (not Next.js)?
- **Speed:** No build step, instant refresh
- **Proven:** Coach Karen UI works well
- **Focus:** Spend time on features, not React config
- **Upgrade path:** Can rebuild in Next.js later with API ready

### Why ChromaDB (not Pinecone)?
- **Local:** No API costs, no latency
- **Simple:** Pip install, done
- **Powerful:** Production-quality embeddings
- **Open source:** Full control

---

## Risk Mitigation

**Risk: Can't finish in 3 days**
- Mitigation: Cut document upload if needed, focus on context + coaching

**Risk: ChromaDB issues**
- Mitigation: Fallback to simple keyword search

**Risk: UI takes too long**
- Mitigation: Start with Coach Karen UI, minimal changes

**Risk: Coaching engine porting issues**
- Mitigation: Copy entire Coach Karen core/, adjust imports

---

## Implementation Notes

### Port from Coach Karen
**What to port:**
- `src/coach/core/engine.py` → `backend/app/core/engine.py`
- `src/coach/core/config.py` → `backend/app/core/config.py`
- `src/coach/conversation/manager.py` → `backend/app/services/coaching_service.py`
- All skill modules → Keep for coaching mode
- UI patterns → Enhance for Tacit branding

**What to modify:**
- System prompts → Add twin personality
- Add context retrieval to coaching
- Add document search to responses
- Rebrand Coach Karen → Tacit

### New Components
- Context capture service (new)
- Document processing service (new)
- Vector search service (ChromaDB wrapper)
- Unified chat API (combines all modes)

---

## Day-by-Day Checklist

### Day 1
- [ ] Directory structure created
- [ ] FastAPI + ChromaDB setup
- [ ] Port Coach Karen engine
- [ ] Context capture API
- [ ] Context capture UI form
- [ ] Basic chat interface
- [ ] Chat can retrieve contexts
- [ ] Coaching mode works

### Day 2
- [ ] Document upload API
- [ ] Text extraction (PDF, DOCX)
- [ ] ChromaDB embedding pipeline
- [ ] Document search API
- [ ] Document library UI
- [ ] Chat integrates document search
- [ ] Source citations in responses

### Day 3
- [ ] Unified chat experience
- [ ] Context management UI
- [ ] Response quality tuning
- [ ] UI polish and branding
- [ ] Error handling
- [ ] End-to-end testing
- [ ] README and docs
- [ ] Demo-ready

---

## Launch Checklist

Before considering MVP "done":
- [ ] Can log contexts (3 types minimum)
- [ ] Can upload documents (PDF + DOCX)
- [ ] Can chat and get relevant answers
- [ ] Sources are cited
- [ ] Coaching mode works
- [ ] UI is clean and professional
- [ ] No critical bugs
- [ ] Fast response times (<2s)
- [ ] Works in Chrome/Safari/Firefox
- [ ] Local deployment documented

---

**Last Updated:** 2025-12-20
**Status:** Day 1 Starting
**Target Completion:** 2025-12-23
