# Makes tests/ a package under `backend`, so pytest's prepend import mode
# puts the parent of backend/ on sys.path and `backend.*` imports resolve
# the same way they do in production.
