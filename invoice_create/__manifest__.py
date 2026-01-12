{
    'name': 'Invoice Create ',
    'version': '1.0',
    'category': 'Contacts',
    'summary': 'Automatically creates invoice if we confirm the sale order.',
    'description': """
Create invoice directly
============================
when the sale order is confirm the invoice is automatically created.
    """,
    'author': 'Captivea',
    'website': 'https://captivea.com',
    'depends': ['base','sale'],
    'data': [
        'security/ir.model.access.csv',
        'views/sale_order_view.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
