from fastapi import FastAPI
app = FastAPI(title="Digital Twin API")
@app.get("/api/test")
def test():
    return {"status": "ok", "message": "Digital Twin API funcionando"}