# LocalAI Desktop — Reusable Boilerplate

> **Authoritative baseline:** [UNIVERSAL-BOILERPLATE.md](UNIVERSAL-BOILERPLATE.md)
> is the locked specification of this boilerplate. It describes only what
> exists and has been verified.

A production-quality, reusable foundation for building small, sellable, local-first Windows AI desktop applications. Built with PySide6, SQLite, and Ollama.

## What This Is

A complete, runnable repository that provides:

- A PySide6 desktop GUI (main window, menus, toolbar, status bar, system tray, settings dialog)
- A clean Ollama service abstraction (configurable host/model, connection testing, streaming chat)
- SQLite persistence with a simple migration system (conversations, messages, settings)
- Persistent JSON configuration stored in the Windows user data directory
- Background worker pattern for non-blocking AI requests
- File logging with rotation and global exception handling
- An isolated product-specific module (`src/app/product/`) for your custom logic
- Automated tests (no Ollama server, internet, or GUI required)
- PyInstaller packaging for Windows (onedir)

## Requirements

- **Windows 10 or 11**
- **Python 3.11+** ([python.org](https://python.org))
- **Ollama** installed and running ([ollama.com](https://ollama.com))

## Quick Start

### 1. Clone and set up virtual environment

```batch
git clone <your-repo-url> local-ai-desktop
cd local-ai-desktop
python -m venv .venv
.venv\Scripts\activate
```

### 2. Install dependencies

```batch
pip install -r requirements.txt
```

### 3. Install Ollama and pull a model

```batch
REM Install Ollama from https://ollama.com
ollama pull llama3.2
```

### 4. Run the application

```batch
python main.py
```

### 5. Run the tests

```batch
python -m pytest tests/ -v
```

### 6. Build the Windows executable

```batch
scripts\build_windows.bat
```

Or manually:

```batch
pyinstaller app.spec --noconfirm --clean
```

The executable will be at `dist\LocalAI-Desktop\LocalAI-Desktop.exe`.

## How to Configure

The application stores its configuration in:

```
%LOCALAPPDATA%\LocalAI Desktop\config.json
```

You can configure:

- **Ollama host** (default: `http://localhost:11434`)
- **Ollama model** (default: `llama3.2`)
- **Request timeout** (default: 120 seconds)
- **System prompt** (default: "You are a helpful assistant.")
- **System tray** (default: enabled)
- **Window size** (saved on close)

Configuration can be changed via the Settings dialog (File > Settings or Ctrl+,).

You can also override any setting from the environment without touching the file.
Variable names are `<PREFIX><KEY>` where the prefix derives from the app name
(`LocalAI Desktop` → `LOCALAI_DESKTOP_`), for example:

```
LOCALAI_DESKTOP_OLLAMA_HOST=http://192.168.1.10:11434
LOCALAI_DESKTOP_OLLAMA_MODEL=mistral
LOCALAI_DESKTOP_OLLAMA_TIMEOUT=30
```

Precedence: environment > config file > built-in defaults. Values are coerced
to the setting's type (numbers, booleans); an unparseable value is ignored
with a warning.

## How to Run

### Development mode

```batch
python main.py
```

Or as a module:

```batch
python -m src.app
```

### Packaged mode

After building, run:

```batch
dist\LocalAI-Desktop\LocalAI-Desktop.exe
```

## How to Run Tests

```batch
python -m pytest tests/ -v
```

Tests cover:
- Configuration loading, saving, and error handling
- SQLite database creation, migrations, and thread safety
- Conversation and message repositories (CRUD, cascade delete)
- Ollama service with mocked HTTP responses (no server needed)
- Product module example
- Utility functions

Tests use temporary directories and do not touch real user data.

## How to Build the Windows Executable

### Prerequisites

- Python 3.11+ with all dependencies installed
- PyInstaller (included in requirements.txt)

### Build

```batch
scripts\build_windows.bat
```

### Output

The build produces a `dist\LocalAI-Desktop\` directory containing:

- `LocalAI-Desktop.exe` — the main executable
- Supporting libraries and resources

Distribute the entire `dist\LocalAI-Desktop\` folder to users.

### Notes

- **onedir** mode is used (not onefile) for faster startup and easier debugging.
- The application stores user data (database, config, logs) in `%LOCALAPPDATA%\LocalAI Desktop\`, separate from the executable.
- Resources (icons, etc.) are bundled and resolved correctly in both development and packaged modes.

## Project Structure

```
project/
├── README.md                  # This file
├── requirements.txt            # Python dependencies
├── main.py                     # Application entry point
├── .gitignore
├── app.spec                    # PyInstaller spec file
├── scripts/
│   └── build_windows.bat       # Windows build script
├── src/
│   └── app/
│       ├── __init__.py
│       ├── __main__.py         # Allows `python -m src.app`
│       ├── core/               # Infrastructure: paths, logging, config, context
│       │   ├── __init__.py
│       │   ├── paths.py        # Path resolution (dev + frozen modes)
│       │   ├── logging_setup.py
│       │   ├── config.py       # ConfigManager (persistent JSON config)
│       │   └── app_context.py  # Composition root
│       ├── data/               # SQLite database, migrations, repositories
│       │   ├── __init__.py
│       │   ├── database.py     # Per-thread connections, WAL mode, migrations
│       │   └── repositories.py # Conversation + Message repositories
│       ├── ai/                 # AI service layer
│       │   ├── __init__.py
│       │   ├── provider.py        # ChatProvider protocol + Ollama adapter
│       │   └── ollama_service.py  # Ollama REST API abstraction
│       ├── product/            # PRODUCT-SPECIFIC CODE GOES HERE
│       │   ├── __init__.py
│       │   └── example_service.py  # Example product service
│       ├── resources/          # Icons, stylesheets, static assets
│       │   └── __init__.py
│       ├── ui/                 # GUI layer
│       │   ├── __init__.py
│       │   ├── main_window.py  # Main application window
│       │   ├── chat_worker.py  # Background worker (QThread)
│       │   ├── settings_dialog.py
│       │   ├── about_dialog.py
│       │   ├── dialogs.py      # Shared dialog helpers
│       │   └── style.py        # Dark theme stylesheet
│       └── utils/              # Utility functions
│           ├── __init__.py
│           └── helpers.py
└── tests/                      # Automated tests
    ├── __init__.py
    ├── conftest.py             # Shared pytest fixtures
    ├── test_app_context.py
    ├── test_app_identity.py
    ├── test_config.py
    ├── test_database.py
    ├── test_repositories.py
    ├── test_ollama_service.py
    ├── test_logging.py
    ├── test_paths.py
    ├── test_product.py
    ├── test_provider.py
    └── test_utils.py
```

## Where Product-Specific Code Belongs

**All product-specific code belongs in `src/app/product/`.**

The boilerplate's generic infrastructure lives outside this directory:

- `src/app/core/` — paths, logging, config, app context
- `src/app/data/` — database, migrations, repositories
- `src/app/ai/` — Ollama service abstraction, plus the `ChatProvider`
  protocol (`src/app/ai/provider.py`): depend on the protocol in product
  code instead of the concrete `OllamaService` so providers can be swapped
  and faked in tests. `OllamaChatProvider` adapts the existing
  `OllamaService` to the protocol without changing it.
- `src/app/ui/` — GUI components
- `src/app/utils/` — utility functions

The `src/app/product/` directory is where you add your application's unique logic. The example service (`example_service.py`) demonstrates how to orchestrate a chat provider (through the `ChatProvider` seam) and database repositories.

## How to Create a New Product from This Boilerplate

1. **Clone the repository** and remove the `.git` directory if you want a clean history.

2. **Rename the application** — update `APP_NAME` (and `APP_VERSION`) in `src/app/core/paths.py`. The window title, tray tooltip, about dialog, Qt application name, user-data directory, and env-var prefix all follow automatically. Two settings are intentionally separate: the Qt organization name in `main.py` (publisher metadata, change only if it differs) and the executable name in `app.spec` (build-time setting).

3. **Replace `src/app/product/`** — delete `example_service.py` and add your own product logic. Use the existing `AppContext` to access services:
   - `self.ctx.config` — persistent configuration
   - `self.ctx.database` — SQLite database access
   - `self.ctx.conversations` — conversation repository
   - `self.ctx.messages` — message repository
   - `self.ctx.ollama` — Ollama AI service
   - `self.ctx.chat_provider` — `ChatProvider` seam wrapping the Ollama service;
     prefer this in product code (it is the protocol-typed provider boundary)

4. **Customize the UI** — modify `src/app/ui/main_window.py` or create new widgets. The existing chat interface is an example you can replace.

5. **Add your own tables** — add a new migration in `src/app/data/database.py` (increment `CURRENT_SCHEMA_VERSION` and add a migration function).

6. **Update the spec file** — change the `name` in `app.spec` and `scripts/build_windows.bat`.

7. **Write tests** — add test files in `tests/` for your product logic.

8. **Build and distribute** — run `scripts/build_windows.bat`.

## Architecture Decisions

### Why no ORM?

SQLite is simple and fast. An ORM adds complexity, dependencies, and a learning curve that isn't justified for small desktop applications. Plain SQL with parameterized queries is more transparent and easier to debug.

### Why per-thread SQLite connections?

Python's `sqlite3` module defaults to `check_same_thread=True`, meaning a connection can only be used by the thread that created it. The recommended pattern is one connection per thread, which we implement using `threading.local()`. WAL mode allows concurrent reads during writes.

### Why QThread subclassing?

The QThread subclass approach with signals/slots is the simplest, most reliable pattern for background work in PySide6. It avoids the complexity of worker-object `moveToThread` while providing clean cancellation via `requestInterruption()` and proper cleanup via `finished` signal.

### Why onedir packaging?

Onedir mode provides faster startup, easier debugging, and simpler resource management compared to onefile. The entire `dist\LocalAI-Desktop\` folder is distributable.

## Dependencies

| Package | Purpose | License |
|---------|---------|---------|
| PySide6 | Qt GUI framework | LGPL |
| requests | HTTP client for Ollama API | Apache 2.0 |
| pyinstaller | Windows packaging | GPL (with special exception) |
| pytest | Testing | MIT |

No web frameworks, cloud SDKs, ORMs, or unnecessary dependencies.

## License

This boilerplate is yours to use, modify, and sell. Add your own license.
