# Gunicorn configuration for Railway deployment
import os
import multiprocessing

# Server socket
bind = f"0.0.0.0:{os.getenv('PORT', 5001)}"

# Worker processes.
#
# gthread, not sync: with 2 sync workers, any two slow database queries
# (routine while the enrichment pipeline is loading the same Postgres)
# pinned the entire site — every other request queued until the edge gave
# up and answered 502 for us. Threads let one worker keep serving while
# other requests wait on the database. The connection pool in app.py is a
# ThreadedConnectionPool to match.
workers = 2
worker_class = "gthread"
threads = 8
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

    # Force re-initialization of connection pool in this worker. NEVER let
    # this raise: a raise here fails the worker boot and gunicorn halts the
    # whole master ('Worker failed to boot') — the app goes dark and the
    # edge 502s everything. init_db_pool itself no longer raises, but keep
    # the belt with the braces.
    try:
        import app
        app.db_pool = None  # Reset the global pool
        app.init_db_pool()  # Initialize fresh pool for this worker
    except Exception as e:
        server.log.error(f"post_fork pool init failed (worker will retry per-request): {e}")


def worker_exit(server, worker):
    """Called just after a worker has been exited."""
    server.log.info(f"Worker exiting (pid: {worker.pid})")
    
    # Clean up database connections
    try:
        import app
        if app.db_pool is not None:
            app.db_pool.closeall()
            app.db_pool = None
    except Exception as e:
        server.log.error(f"worker_exit pool cleanup failed: {e}")
