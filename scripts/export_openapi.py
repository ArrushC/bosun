"""Write the backend OpenAPI schema to a file (used to generate frontend types)."""

import json
import sys

from bosun.main import create_app

app = create_app(run_migrations=False, register_initial_workspace=False)
json.dump(app.openapi(), open(sys.argv[1], "w"), indent=1)
