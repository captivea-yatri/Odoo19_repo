{
    'name': 'Project Task Assignees ',
    'version': '1.0',
    'category': 'Contacts',
    'summary': 'Ensures there atleast one assignees is assigned for thr each task.',
    'description': """
Ensures all project task managers
============================
On the task project manager should be there if there are not any assignees set.
not any task are there without any assignees.
    """,
    'author': 'Captivea',
    'website': 'https://captivea.com',
    'depends': ['base','contacts'],
    'data': [
        'security/ir.model.access.csv',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
