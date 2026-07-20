import logging

import uvicorn

from app import config

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")

if __name__ == "__main__":
    uvicorn.run("app.web:app", host="0.0.0.0", port=config.PORT)
