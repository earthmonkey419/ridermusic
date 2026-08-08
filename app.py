from flask import Flask

from ridermusic_sessions import register_join_route, teardown_db, require_active_session
from ridermusic_admin import register_admin_routes

app = Flask(__name__)
app.teardown_appcontext(teardown_db)
register_join_route(app)
register_admin_routes(app)


@app.route("/guest")
@require_active_session
def guest():
    return "Guest portal placeholder — session is valid."


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=6869)
