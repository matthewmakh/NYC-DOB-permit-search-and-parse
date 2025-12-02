# Gunicorn configuration for Railway deployment
import os
import multiprocessing

# Server socket
bind = f"0.0.0.0:{os.getenv('PORT', 5001)}"

# Worker processes
workers = 2
worker_class = "sync"
worker_connections = 1000
timeout = 120

# Logging
accesslog = "-"
errorlog = "-"
loglevel = "info"

# Server mechanics
daemon = False
pidfile = None
user = None
group = None
tmp_upload_dir = None

# Worker lifecycle hooks
def post_fork(server, worker):
    """Called just after a worker has been forked.
    
    This ensures each worker gets its own connection pool
    instead of sharing the parent's connections.
    """
    server.log.info(f"Worker spawned (pid: {worker.pid})")
    
    # Force re-initialization of connection pool in this worker
    import app
    app.db_pool = None  # Reset the global pool
    app.init_db_pool()  # Initialize fresh pool for this worker


def worker_exit(server, worker):
    """Called just after a worker has been exited."""
    server.log.info(f"Worker exiting (pid: {worker.pid})")
    
    # Clean up database connections
    import app
    if app.db_pool is not None:
        app.db_pool.closeall()
        app.db_pool = None
