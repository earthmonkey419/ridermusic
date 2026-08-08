from flask import Flask

from ridermusic_sessions import register_join_route, teardown_db
from ridermusic_admin import register_admin_routes, register_admin_dashboard_route, register_guide_route
from ridermusic_sign import register_sign_routes
from ridermusic_player import register_player_routes
from ridermusic_guest import register_guest_routes, register_guest_page_route
from ridermusic_playback import register_playback_routes, register_player_state_route, register_player_page_route

app = Flask(__name__)
app.teardown_appcontext(teardown_db)
register_join_route(app)
register_admin_routes(app)
register_admin_dashboard_route(app)
register_guide_route(app)
register_sign_routes(app)
register_player_routes(app)
register_guest_routes(app)
register_guest_page_route(app)
register_playback_routes(app)
register_player_state_route(app)
register_player_page_route(app)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=6869)
