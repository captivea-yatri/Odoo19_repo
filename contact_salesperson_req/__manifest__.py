{
    'name': 'Contact Salesperson Sync',
    'version': '1.0',
    'category': 'Contacts',
    'summary': 'Automatically sync salesperson between company and its child contacts.',
    'description': """
Sync Salesperson on Contacts
============================
- When a company contact’s salesperson is changed or cleared, all its child contacts are updated automatically.
- New child contacts inherit their parent company’s salesperson.
    """,
    'author': 'Your Name',
    'website': 'https://yourcompany.com',
    'depends': ['base'],
    'data': [
        'security/ir.model.access.csv',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
