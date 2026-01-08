{
    'name': "Timesheet Restriction",
    'version': '1.0',
    'category': 'Human Resources',
    'summary': 'Restrict editing timesheets of past months except for authorized users',
    'description': """
This module restricts employees from modifying timesheets belonging to past months.
Only users with the group 'Can Reduce Past Timesheets' can edit or reduce them.
    """,
    'author': "Your Name / Company",
    'depends': ['analytic', 'project'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'views/timesheet_view.xml',
    ],
    'installable': True,
    'application': False,
}
