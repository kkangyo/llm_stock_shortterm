import sqlite3

conn = sqlite3.connect("data/news_pipeline.db")
conn.row_factory = sqlite3.Row

total = conn.execute("SELECT COUNT(*) c FROM pipeline_records").fetchone()["c"]
relevant = conn.execute(
    "SELECT COUNT(*) c FROM pipeline_records WHERE ollama_is_relevant=1"
).fetchone()["c"]
print(f"전체 처리: {total}건 / 1차 필터 통과(호재-악재 관련): {relevant}건")
print()
print("--- 1차 필터 통과 목록 ---")
for row in conn.execute(
    "SELECT title, ollama_sentiment, ollama_candidate_tickers, matched_stocks, "
    "claude_assessments, created_at "
    "FROM pipeline_records WHERE ollama_is_relevant=1 ORDER BY created_at"
):
    print(f"[{row['ollama_sentiment']}] {row['title']}  후보종목={row['ollama_candidate_tickers']}")
    print(f"    실종목검증={row['matched_stocks']}")
    if row["claude_assessments"] and row["claude_assessments"] != "[]":
        print(f"    Claude종목별판단={row['claude_assessments']}")
