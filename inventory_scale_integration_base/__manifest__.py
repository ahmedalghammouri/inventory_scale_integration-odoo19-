{
'name': 'Scale Integration - Base',
'version': '19.0.1.0.0',
'category': 'Inventory/Stock',
'summary': 'Core weighing scale integration with stock operations',
'author': 'Gemy',
'website': 'https://www.example.com',
'license': 'LGPL-3',
'depends': ['stock', 'mail', 'web'],
'data': [
'data/sequences.xml',
'security/security.xml',
'security/ir.model.access.csv',
'views/truck_weighing_views.xml',
'views/truck_fleet_views.xml',
'views/res_users_views.xml',
'views/stock_picking_views.xml',
'views/weighing_scale_views.xml',
'views/product_views.xml',
'views/weighing_overview_views.xml',

'views/menu_items_views.xml',
],
'assets': {
'web.assets_backend': [
'inventory_scale_integration_base/static/src/js/weighing_dashboard.js',
'inventory_scale_integration_base/static/src/xml/weighing_dashboard.xml',
'inventory_scale_integration_base/static/src/scss/weighing_dashboard.scss',
],
'web.assets_web_dark': [
'inventory_scale_integration_base/static/src/scss/weighing_dashboard.scss',
],
},
'installable': True,
'application': True,
}