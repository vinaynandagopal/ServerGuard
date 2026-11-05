"""Start ServerGuard:  python run.py"""

import logging

from app import create_app
from app.config import Config


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    app = create_app()
    # No debug mode and no reloader: the reloader would start a second
    # collector, and debug mode exposes an interactive console.
    app.run(host=Config.HOST, port=Config.PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
