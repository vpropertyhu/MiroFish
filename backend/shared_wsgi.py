"""One-process shared deployment; provider credentials stay server-side."""
from app.shared_host import create_shared_app
app = create_shared_app()
