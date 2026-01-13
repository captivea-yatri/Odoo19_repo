{
    'name': 'Lot Serial View',
    'version': '1.0',
    'category': 'Sales',
    'summary': 'On the sale order lot/serail number is visible and choosen .',
    'description': """
Add lot/serial No at invoice rather than do manually
============================
Set or choose the lot/serail no for the selected product which we want to sell. 
    """,
    'author': 'Captivea',
    'website': 'https://captivea.com',
    'depends': ['sale_stock', 'account','l10n_cl'],
    'data': [
        'views/lot_serial_view.xml',
         'views/account_move_line_view.xml',
         'views/account_invoice_report.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
