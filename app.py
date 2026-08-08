from flask import Flask

from ridermusic_sessions import register_join_route, teardown_db, require_active_session
from ridermusic_admin import register_admin_routes
from ridermusic_player import register_player_routes
from ridermusic_guest import register_guest_routes
from ridermusic_playback import register_playback_routes, register_player_state_route, register_player_page_route

app = Flask(__name__)
app.teardown_appcontext(teardown_db)
register_join_route(app)
register_admin_routes(app)
register_player_routes(app)
register_guest_routes(app)
register_playback_routes(app)
register_player_state_route(app)
register_player_page_route(app)


@app.route("/guest")
@require_active_session
def guest():
    return "Guest portal placeholder — session is valid."


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=6869)
