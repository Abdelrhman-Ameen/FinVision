import argparse
import json

import uvicorn

from .retrieval import prepare
from .settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local Egyptian Startup RAG chatbot")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("prepare", help="Download the embedding model and build local indexes")
    serve = commands.add_parser("serve", help="Start the localhost web server")
    serve.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.command == "prepare":
        print(json.dumps(prepare(Settings.load().cache), indent=2))
    else:
        uvicorn.run("rag.app:app", host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
