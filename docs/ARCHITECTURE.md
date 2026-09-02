# Oli-Bot Architecture & Diagrams

## System Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                         User Terminal                            │
│                    (Textual TUI Application)                     │
└────────────────────────┬─────────────────────────────────────────┘
                         │
                         ▼
            ┌────────────────────────────┐
            │    chat.py (Main Loop)     │
            │  - User input handling     │
            │  - Command processing      │
            │  - Markdown rendering      │
            │  - Session management      │
            └────────────┬───────────────┘
                         │
                         ▼
            ┌────────────────────────────┐
            │  Agent (agent.py)          │
            │  - Process user input      │
            │  - Generate tool calls     │
            │  - Aggregate results       │
            │  - Maintain context        │
            └────────────┬───────────────┘
                         │
                ┌────────┴────────────┐
                ▼                     ▼
        ┌──────────────────┐ ┌──────────────────────┐
        │  Tool Manager    │ │ Permission System    │
        │  (tools/*)       │ │ (profiles/*.py)      │
        │                  │ │                      │
        │ - read_file      │ │ - Validate actions   │
        │ - write_file     │ │ - Grant/deny         │
        │ - run_command    │ │ - Session tracking   │
        │ - websearch      │ │ - Audit logging      │
        │ - notebook       │ │ - Workspace scope    │
        │ - dispatch       │ │                      │
        └────────┬─────────┘ └──────────────────────┘
                 │
                 ▼
        ┌──────────────────────────────┐
        │   Backend Abstraction        │
        │   (backend.py)               │
        │                              │
        │ - Unified interface          │
        │ - Streaming aggregation      │
        │ - Tool call parsing          │
        │ - Token management           │
        └─────┬──────────────────┬─────┘
              │                  │
    ┌─────────┴───────┐   ┌──────┴────────┐
    ▼                 ▼   ▼               ▼
┌────────────┐  ┌──────────────┐  ┌────────────────┐
│  Ollama    │  │  OpenAI      │  │ HuggingFace    │
│  (Local)   │  │  (Remote)    │  │ (Remote/Local) │
└────────────┘  └──────────────┘  └────────────────┘

                    ▼ (Response Stream)

            ┌────────────────────────────┐
            │  TUI Display               │
            │  - Markdown rendering      │
            │  - Streaming updates       │
            │  - Status indicators       │
            └────────────────────────────┘

                    ▼ (Storage)

            ┌────────────────────────────┐
            │  Session Persistence       │
            │  ~/.config/oli/sessions/   │
            │  (JSON + metadata)         │
            └────────────────────────────┘
```

---

## Data Flow Diagram

```
User Input
    │
    ▼
┌─────────────────────────────────────────┐
│ chat.py: Parse Input                    │
│ - Command vs. message                   │
│ - Session context lookup                │
└─────────────┬───────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────┐
│ Is it a command? (/help, /config, etc.) │
├─────────────────────────────────────────┤
│ Yes ──────► Execute Command Handler     │
│            └──► Display Result          │
└─────────────────────────────────────────┘
              │ No
              ▼
┌─────────────────────────────────────────┐
│ agent.py: Process Message               │
│ - Add to conversation history           │
│ - Generate system prompt                │
│ - Format for LLM                        │
└─────────────┬───────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────┐
│ backend.py: Stream LLM Response         │
│ - Connect to backend (Ollama/OpenAI)    │
│ - Stream tokens                         │
│ - Buffer tool calls                     │
└─────────────┬───────────────────────────┘
              │
              ├─────► Display tokens in TUI
              │
              ▼
┌─────────────────────────────────────────┐
│ Parse Tool Calls                        │
│ - Extract function calls                │
│ - Validate schema                       │
└─────────────┬───────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────┐
│ Permission Check (profiles/*)           │
│ - Tool enabled in profile?              │
│ - Workspace boundary OK?                │
│ - User approval needed?                 │
└─────────────┬───────────────────────────┘
              │
       ┌──────┴───┐
       ▼          ▼
      DENY      GRANT
       │          │
       ▼          ▼
    Error   ┌──────────────────────┐
            │ tools/manager.py     │
            │ Route to tool        │
            └──────┬───────────────┘
                   │
                   ▼
            ┌──────────────────────┐
            │ tools/*.py           │
            │ Execute tool         │
            │ (files, shell, web)  │
            └──────┬───────────────┘
                   │
                   ▼
            ┌──────────────────────┐
            │ Aggregate Result     │
            │ into context         │
            └──────┬───────────────┘
                   │
              Continue Loop?
                   │
          ┌────────┴────────┐
          ▼                 ▼
         YES               NO
          │                 │
          └──► Repeat    Store Result
              from        Save Session
              "Stream     Display Final
              LLM         Response
              Response"
```

## Permission Flow Diagram

```
Tool Call Generated by Agent
    │
    ▼
┌───────────────────────────────────────────┐
│ Permission Check (3-layer model)          │
└─────────┬───────────────────┬─────────────┘
          │                   │
    ┌─────▼─────┐        ┌────▼──────┐
    │ Profile   │        │ Session   │
    │ Level     │        │ Level     │
    │           │        │           │
    │ manifests │        │ Runtime   │
    │ allow/deny│        │ approvals │
    └─────┬─────┘        └────┬──────┘
          │                   │
          └───────┬───────────┘
                  ▼
    ┌────────────────────────────────────────┐
    │ Tool-Level Checks                      │
    │ - SSRF validation (web tools)          │
    │ - Shell allowlist (shell tool)         │
    │ - Sensitive files blocklist            │
    │ - Workspace boundary check             │
    │ - Path traversal detection             │
    └────────────┬───────────────────────────┘
                 │
          ┌──────┴────┐
          ▼           ▼
         PASS        FAIL
          │           │
          │           ▼
          │    ┌────────────┐
          │    │ Deny tool  │
          │    │ call, warn │
          │    │ user       │
          │    └────────────┘
          │
          ▼
    ┌──────────────────────┐
    │ Need approval?       │
    │ (e.g., write file)   │
    └────────┬─────────────┘
             │
         ┌───┴────┐
         ▼        ▼
        Yes       No
         │        │
         ▼        │
    Prompt User   │
         │        │
    ┌────┴────┐   │
    ▼         ▼   │
   Yes       No   │
    │        │    │
    ▼        ▼     ▼
 GRANT    DENY  EXECUTE
    │       │       │
    └───────┴───────┘
            │
            ▼
    ┌──────────────────┐
    │ Execute Tool     │
    │ Get Result       │
    └──────────────────┘
```

---

## Backend Selection Logic

```
┌─────────────────────────────────────┐
│ Backend Selection (Priority Order)  │
└─────────────────────────────────────┘

1. Command-line flag
   └─ python chat.py --backend openai

2. Environment variable
   └─ OLI_BACKEND=openai

3. .env file
   └─ OLI_BACKEND=ollama

4. Config file (~/.config/oli/config.json)
   └─ "backend": "transformers"

5. Session setting (if resuming)
   └─ Load from session metadata

6. Default (Ollama)
   └─ http://localhost:11434

┌──────────────────────────────────────┐
│ Supported Backends                   │
├──────────────────────────────────────┤
│ ollama          - Local via HTTP     │
│ openai          - OpenAI API         │
│ huggingface     - HF Inference API   │
│ transformers    - Local HF models    │
└──────────────────────────────────────┘
```

---

## Agent Pooling Architecture

```
┌────────────────────────────────────┐
│ Root Agent                         │
│ - Received user task               │
│ - Decides dispatch plan            │
│ - Aggregates results               │
└────────┬─────────────────────┬─────┘
         │                     │
    Can dispatch?          ┌───▼────┐
         │                 │ Yes    │
         │                 └────┬───┘
         │                      │
         ▼                      ▼
    ┌─────────────┐      ┌──────────────────┐
    │ Load        │      │ builtin__dispatch│
    │ agents.yaml │      │ (tool call)      │
    └─────────────┘      └────────┬─────────┘
                                  │
                    ┌─────────────┼─────────────┐
                    │             │             │
                    ▼             ▼             ▼
            ┌──────────┐  ┌──────────┐  ┌─────────┐
            │ Analyst  │  │ Searcher │  │ Coder   │
            │ Agent    │  │ Agent    │  │ Agent   │
            │          │  │          │  │         │
            │ Local    │  │ OpenAI   │  │ Remote  │
            │ Ollama   │  │ gpt-4    │  │ HF      │
            └────┬─────┘  └────┬─────┘  └────┬────┘
                 │             │             │
       ┌─────────┴─────────────┴─────────────┘
       │
       │ Execute in parallel
       │
       ▼
    Results aggregated
    │
    ├─ Analyst result: {...}
    ├─ Searcher result: {...}
    ├─ Coder result: {...}
    │
    ▼
    Root agent processes results
    │
    └─ Return final response to user
```

---

## State Machine: Chat Session

```
                    Start
                     │
                     ▼
            ┌─────────────────┐
            │ New Session     │
            │ (or Resume)     │
            └────────┬────────┘
                     │
                     ▼
         ┌──────────────────────┐
         │ Wait for User Input  │
         └────────┬─────────────┘
                  │
              ┌───┴────┐
              ▼        ▼
          Command   Message
              │        │
              │        └──────► Add to History
              │                      │
              │                      ▼
              │           ┌──────────────────┐
              │           │ Generate LLM     │
              │           │ Response         │
              └──────────►│ (streaming)      │
                          └────┬─────────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Parse Tool Calls    │
                    │ (if any)            │
                    └────────┬────────────┘
                             │
                         ┌───┴────┐
                         ▼        ▼
                      Yes        No
                         │        │
                         ▼        ▼
                    Execute   Display
                    Tools     Response
                         │        │
                         ▼        ▼
                    ┌──────────────────┐
                    │ Save Session     │
                    │ Update history   │
                    └────────┬─────────┘
                             │
                             ▼
                    Continue loop?
                             │
                         ┌───┴────┐
                         ▼        ▼
                       Yes       No
                        │        │
                    Loop ◄┘      └──► Exit
                                     │
                                     ▼
                              Session Closed
```

---

## Configuration Precedence (Lowest to Highest)

```
┌─────────────────────────────────────────────┐
│ Configuration Precedence Chain              │
├─────────────────────────────────────────────┤
│ 1. Hardcoded defaults                       │
│    └─ backend = "ollama"                    │
│       url = "http://localhost:11434"        │
│                                             │
│ 2. Global config file                       │
│    └─ ~/.config/oli/config.json             │
│       (if exists)                           │
│                                             │
│ 3. .env file (project directory)            │
│    └─ .env                                  │
│                                             │
│ 4. Environment variables                    │
│    └─ $OLI_BACKEND                          │
│       $OLI_MODEL                            │
│       $OLI_OPENAI_API_KEY                   │
│                                             │
│ 5. Session metadata                         │
│    └─ ~/.config/oli/sessions/<uuid>.json    │
│       (when resuming)                       │
│                                             │
│ 6. Command-line flags (HIGHEST)             │
│    └─ --backend openai                      │
│       --model gpt-4                         │
│       --profile analyst                     │
└─────────────────────────────────────────────┘
```

---

## Tool Execution Flow

```
Agent generates tool call:
{
  "tool": "builtin__read_file",
  "parameters": {
    "file_path": "/path/to/file.txt"
  }
}
   │
   ▼
┌────────────────────────────────────┐
│ tools/manager.py                   │
│ Dispatch to handler                │
└────────┬─────────────────────────┬┘
         │                         │
         ▼                         ▼
    Exists?              ┌────────────────┐
         │               │ Load handler   │
     ┌───┴────┐          │ module         │
     ▼        ▼          └────────────────┘
   Yes       No
    │        │
    │        ▼
    │     Return error
    │     "Tool not found"
    │
    ▼
┌────────────────────────────────────┐
│ Permission Check                   │
│ - Profile allows tool?             │
│ - Session grant exists?            │
│ - Tool-level checks pass?          │
└────────┬───────────────────────────┘
         │
     ┌───┴────┐
     ▼        ▼
  Allowed  Denied
    │        │
    │        ▼
    │    Return error
    │    "Permission denied"
    │
    ▼
┌────────────────────────────────────┐
│ Validate Parameters                │
│ - Schema validation                │
│ - Type checking                    │
│ - Required fields                  │
└────────┬───────────────────────────┘
         │
     ┌───┴────┐
     ▼        ▼
  Valid    Invalid
    │        │
    │        ▼
    │    Return error
    │    "Invalid parameters"
    │
    ▼
┌────────────────────────────────────┐
│ Execute Tool Function              │
│ - Run implementation               │
│ - Capture output                   │
│ - Handle errors                    │
└────────┬───────────────────────────┘
         │
         ▼
┌────────────────────────────────────┐
│ Format Result                      │
│ - Convert to string                │
│ - Truncate if needed               │
│ - Sanitize sensitive data          │
└────────┬───────────────────────────┘
         │
         ▼
    Return result to Agent
```

---

## Workspace Scoping

```
┌──────────────────────────────────────────┐
│ Workspace: /home/user/project            │
│ (Set via /workspace set /home/user/project)
└──────────────────────────────────────────┘
         │
         ▼
    All file operations checked:

┌──────────────────────────────────────────┐
│ Allowed Paths                            │
├──────────────────────────────────────────┤
│ ✅ /home/user/project/file.txt           │
│ ✅ /home/user/project/data/input.csv     │
│ ✅ /home/user/project/../project/file.txt│
│    (normalized to within workspace)      │
└──────────────────────────────────────────┘

┌──────────────────────────────────────────┐
│ Denied Paths                             │
├──────────────────────────────────────────┤
│ ❌ /etc/passwd                           │
│ ❌ /home/user/private/secret.key         │
│ ❌ /home/user/project/../../etc/hosts    │
│    (resolves outside workspace)          │
│ ❌ ~/.ssh/id_rsa                         │
│    (sensitive file)                      │
└──────────────────────────────────────────┘
```

---

## Error Handling Flow

```
┌──────────────────────────────────┐
│ Error Occurs in Tool             │
└────────────┬─────────────────────┘
             │
             ▼
    ┌────────────────────┐
    │ Error Type?        │
    └────┬───────┬──────┬┘
         │       │      │
    ┌────▼┐  ┌───▼──┐ ┌─▼─────┐
    │User │  │Tool  │ │System  │
    │Error│  │Error │ │Error   │
    └──┬─┘  └───┬──┘ └──┬──────┘
       │        │       │
       ▼        ▼       ▼
    Show      Log to  Crash
    in chat   backend  recovery
    │        │        │
    │        ▼        ▼
    │     ndjson      Fatal?
    │     logs
    │
    ├──────► Continue or retry?
    │
    └──────► Save session
             Return error message
```

---

## Session File Structure

```
~/.config/oli/
├── config.json
│   └─ Global settings
│
├── sessions/
│   ├─ 550e8400-e29b-41d4.json
│   ├─ 6ba7b810-9dad-11d1.json
│   └─ ...
│
└─ profiles/  (custom)
   ├─ my-analyst/
   │   ├─ profile.json
   │   ├─ SKILLS.md
   │   └─ AGENTS.md
   └─ my-researcher/
       ├─ profile.json
       ├─ SKILLS.md
       └─ AGENTS.md
```

---

## Textual TUI Component Hierarchy

```
┌─────────────────────────────────────────┐
│ App (ChatApp)                           │
│ - Main Textual application              │
│ - Event loop management                 │
│ - Screen stack                          │
└────────────┬────────────────────────────┘
             │
    ┌────────┼────────┬──────────┐
    ▼        ▼        ▼          ▼
┌────────┐ ┌─────┐ ┌──────┐ ┌──────────┐
│Primary │ │Mode │ │Config│ │ Command  │
│Screen  │ │Bar  │ │Modal │ │ Palette  │
└───┬────┘ └─────┘ └──────┘ └──────────┘
    │
    ├─ Chat Display (scrollable)
    ├─ User Input (editable)
    ├─ Status Bar (info)
    └─ Context Display (model/profile)
```

---

## Useful Architecture Patterns

| Pattern | Usage | Location |
|---------|-------|----------|
| **Factory** | Backend creation | backend.py |
| **Strategy** | Tool implementations | tools/*.py |
| **Observer** | Textual events | views.py |
| **Builder** | Config precedence | settings.py |
| **Adapter** | Backend interface | backend.py |
| **Registry** | Tool management | tools/manager.py |
| **Template Method** | Agent processing | agent.py |

---

## Deployment Architecture

```
┌──────────────────────────────────┐
│ Local Development                │
├──────────────────────────────────┤
│ User ──► Terminal ──► chat.py    │
│          ↓                       │
│          Ollama (local)          │
└──────────────────────────────────┘

┌──────────────────────────────────┐
│ Team with Pool                   │
├──────────────────────────────────┤
│ Users ──► chat.py (root)         │
│            ├─► Analyst (OpenAI)  │
│            ├─► Search (Claude)   │
│            └─► Coder (Local)     │
└──────────────────────────────────┘

┌──────────────────────────────────┐
│ Docker Container                 │
├──────────────────────────────────┤
│ Container ──► chat.py            │
│                ├─► OpenAI API    │
│                └─► HF Inference  │
│                    API           │
└──────────────────────────────────┘

┌──────────────────────────────────┐
│ Hybrid (Local + Cloud)           │
├──────────────────────────────────┤
│ User ──► chat.py (Ollama root)   │
│           ├─► Quick tasks (GPT)  │
│           ├─► Slow tasks (HF)    │
│           └─► Local analysis     │
└──────────────────────────────────┘
```

This comprehensive architecture reference provides a complete understanding of how oli-bot components interact, data flows through the system, and key decision points in execution paths.
