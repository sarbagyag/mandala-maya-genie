import os
import uvicorn
from fastapi import FastAPI

from api.routes import router

app = FastAPI(title="Maya Genie - Cognitive Core")
app.include_router(router)

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8083"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
