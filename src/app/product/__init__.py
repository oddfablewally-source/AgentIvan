"""
Product-specific module.

This is where YOUR application's specific logic lives. The boilerplate's
generic infrastructure (config, database, AI service, UI framework) is
outside this directory. Clone the boilerplate and replace/add code here
without touching the foundation.

The example product is a simple chat application. To build your own
product:

1. Replace or extend the code in this directory.
2. The AppContext already provides:
   - self.ctx.config       - persistent configuration
   - self.ctx.database      - SQLite database access
   - self.ctx.conversations - conversation repository
   - self.ctx.messages      - message repository
   - self.ctx.ollama        - Ollama AI service
   - self.ctx.chat_provider - ChatProvider seam (prefer this over self.ctx.ollama
     in product code; it is the protocol-typed provider boundary)

3. Create your own UI in src/app/ui/ or wire your product logic into
   the existing MainWindow.

Keep product logic isolated here so the foundation remains reusable.
"""
