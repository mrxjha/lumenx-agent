web: uvicorn server.app:app --host 0.0.0.0 --port $PORT
dashboard: streamlit run dashboard/app.py --server.headless true --server.address 0.0.0.0 --server.port $PORT --browser.gatherUsageStats false
poller: python -m agent.poller
