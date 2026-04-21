from flask import Blueprint, render_template
from app.routes.upload import get_server_stats
main_bp = Blueprint('main_bp', __name__)


@main_bp.route('/')
def index():
    server_stats = get_server_stats()
    projects = [
        {
            'name': 'M1 BFT Log System',
            'desc': 'BFT Station log center, supporting tree view and advanced search',
            'status': 'Active',
            'tree_url': 'tree_bp.tree_view',
            'search_url': 'search_bp.index',
            'icon': 'bi-gear',
            'color': 'primary'
        },
        {
            'name': 'BFT Log Analysis',
            'desc': 'BFT test log analysis module is currently in the environment debugging phase.',
            'status': 'Pending',
            'tree_url': None,
            'search_url': None,
            'icon': 'bi-gear',
            'color': 'secondary' # 待定状态用灰色
        }
    ]

    return render_template('index.html', projects=projects, stats=server_stats)