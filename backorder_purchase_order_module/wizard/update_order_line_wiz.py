from odoo import models, fields, api, _
from odoo.exceptions import UserError


class BackorderPurchaseUpdateWizard(models.TransientModel):
    _name = 'backorder.purchase.update.wizard'
    _description = 'Wizard to update Backorder Purchase Order'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    order_id = fields.Many2one('backorder.purchase.order', string='Backorder Order', required=True)
    vendor_id = fields.Many2one('res.partner', string='Vendor')
    line_ids = fields.One2many('backorder.purchase.update.wizard.line', 'wizard_id', string='Order Lines')

    # ------------------------------------------------------------
    # Default load
    # ------------------------------------------------------------
    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        active_id = self.env.context.get('active_id')
        if not active_id:
            return res

        order = self.env['backorder.purchase.order'].browse(active_id)
        res['order_id'] = order.id
        res['vendor_id'] = order.vendor_id.id
        lines = []
        for line in order.order_line.filtered(lambda l: l.active):
            lines.append((0, 0, {
                'line_id': line.id,
                'product_id': line.product_id.id,
                'vendor_id': line.vendor_id.id,
                'quantity': line.quantity,
                'price': line.price,
            }))
        res['line_ids'] = lines
        return res

    # ------------------------------------------------------------
    # Apply Updates
    # ------------------------------------------------------------
    def action_apply_updates(self):
        """Safely update a confirmed order — temporarily sets to draft, updates, then reconfirms."""
        self.ensure_one()
        order = self.order_id
        ctx = dict(self.env.context, allow_wizard_write=True)

        # 1️⃣ Temporarily set to draft to allow modifications
        if order.state == 'confirm':
            order.with_context(ctx).write({'state': 'draft'})

        # 2️⃣ Capture old vendor
        old_vendor = order.vendor_id

        # 3️⃣ Update Vendor if changed
        if self.vendor_id and self.vendor_id != old_vendor:
            order.with_context(ctx).write({'vendor_id': self.vendor_id.id})

            # ✅ Update journal entry partner
            if order.account_move_id:
                move = order.account_move_id
                # If posted, set to draft
                if move.state == 'posted':
                    move.button_draft()
                # Update partner in move & all lines
                move.write({'partner_id': self.vendor_id.id})
                move.line_ids.write({'partner_id': self.vendor_id.id})
                # Repost move
                move.action_post()

        # 3️⃣ Update order lines
        existing_line_ids = [l.line_id.id for l in self.line_ids if l.line_id]
        for line in order.order_line:
            if line.id not in existing_line_ids:
                line.with_context(ctx).write({'active': False})

        total_qty = 0
        total_value = 0
        for wiz_line in self.line_ids:
            vals = {
                'product_id': wiz_line.product_id.id,
                'vendor_id': wiz_line.vendor_id.id,
                'quantity': wiz_line.quantity,
                'price': wiz_line.price,
            }
            if wiz_line.line_id:
                wiz_line.line_id.with_context(ctx).write(vals)
                line = wiz_line.line_id
            else:
                vals['order_id'] = order.id
                line = self.env['backorder.purchase.order.line'].create(vals)

            total_qty += line.quantity
            total_value += line.total

        # 4️⃣ Update stock moves
        self._update_or_create_stock_moves(order)

        # 5️⃣ Recreate journal entry
        self._recreate_journal_entry(order)

        # 6️⃣ Update product cost/value
        for line in order.order_line.filtered('active'):
            self._recompute_product_cost(line.product_id)

        # 7️⃣ Reconfirm the order
        order.with_context(ctx).write({'state': 'confirm'})
        if hasattr(order, 'message_post'):
            order.message_post(
                body=_("Vendor changed from <b>%s</b> to <b>%s</b> via Update Wizard.") %
                     (old_vendor.display_name, self.vendor_id.display_name),
                subtype_xmlid="mail.mt_note"
            )
        if order.account_move_id:
            order.account_move_id.message_post(
                body=_("Vendor updated from <b>%s</b> to <b>%s</b> (Backorder: %s)") %
                     (old_vendor.display_name, self.vendor_id.display_name, order.name),
                subtype_xmlid="mail.mt_note"
            )

        return {'type': 'ir.actions.act_window_close'}

    # ------------------------------------------------------------
    # Stock move handling
    # ------------------------------------------------------------
    def _update_or_create_stock_moves(self, order):
        """Recreate stock moves based on updated lines — ensure one move per product per vendor, correct total value."""
        StockMove = self.env['stock.move']
        StockMoveLine = self.env['stock.move.line']

        new_moves = []


        # Group lines by vendor
        vendor_groups = {}
        for line in order.order_line.filtered('active'):
            vendor_groups.setdefault(line.vendor_id, []).append(line)

        for vendor, lines in vendor_groups.items():
            vendor_location = vendor.property_stock_supplier or self.env.ref('stock.stock_location_suppliers')
            stock_location = self.env.ref('stock.stock_location_stock')

            # Group by product (aggregate qty and weighted avg price)
            product_groups = {}
            for line in lines:
                product = line.product_id
                key = (product.id, vendor.id)
                if key not in product_groups:
                    product_groups[key] = {
                        'product': product,
                        'qty': line.quantity,
                        'price': line.price,
                    }
                else:
                    total_qty = product_groups[key]['qty'] + line.quantity
                    if total_qty > 0:
                        product_groups[key]['price'] = (
                                                               (product_groups[key]['price'] * product_groups[key][
                                                                   'qty']) +
                                                               (line.price * line.quantity)
                                                       ) / total_qty
                    product_groups[key]['qty'] = total_qty

            existing_moves = order.move_ids.filtered(lambda m: m.partner_id == vendor)

            # ✅ Remove old moves for products not in new lines
            existing_products = [p['product'].id for p in product_groups.values()]
            for move in existing_moves:
                if move.product_id.id not in existing_products:
                    if move.state == 'done':
                        self._create_return_move(move)
                    else:
                        move.unlink()

            # ✅ Handle each grouped product
            for data in product_groups.values():
                product = data['product']
                qty = data['qty']
                price_unit = data['price']
                total_value = qty * price_unit
                create_moves_list = {'product_id': product.id,
                                     'product_uom': product.uom_id.id,
                                     'product_uom_qty': qty,
                                     'price_unit': price_unit,
                                     'value': total_value,
                                     'location_id': vendor_location.id,
                                     'location_dest_id': stock_location.id,
                                     'origin': order.name,
                                     'reference': order.name,
                                     'company_id': order.company_id.id,
                                     'partner_id': vendor.id,
                                     'state': 'draft', }


                # ✅ Check if move already exists for this product & vendor
                existing_move = existing_moves.filtered(lambda m: m.product_id == product)

                # If multiple exist, keep one and delete duplicates
                if len(existing_move) > 1:
                    existing_move[1:].unlink()
                    existing_move = existing_move[:1]

                if existing_move:
                    move = existing_move[0]



                    # ✅ If existing move found, update it instead of creating a new one
                    if move.state == 'done':
                        self._create_return_move(move)
                        move = StockMove.create(create_moves_list)
                    else:
                        move.write({
                            'product_uom_qty': qty,
                            'price_unit': price_unit,
                            'value': total_value,
                            'origin': order.name,
                            'reference': order.name,
                        })

                        # ✅ Update or create move line
                        if move.move_line_ids:
                            move.move_line_ids.write({
                                'qty_done': qty,
                                'location_id': vendor_location.id,
                                'location_dest_id': stock_location.id,
                            })
                        else:
                            StockMoveLine.create({'move_id': move.id,
                                             'product_id': product.id,
                                             'product_uom_id': product.uom_id.id,
                                             'qty_done': qty,
                                             'location_id': vendor_location.id,
                                             'location_dest_id': stock_location.id,
                                             'company_id': order.company_id.id,})
                else:
                    # ✅ Only create a new move if none exists
                    move = StockMove.create(create_moves_list)

                    StockMoveLine.create({'move_id': move.id,
                                             'product_id': product.id,
                                             'product_uom_id': product.uom_id.id,
                                             'qty_done': qty,
                                             'location_id': vendor_location.id,
                                             'location_dest_id': stock_location.id,
                                             'company_id': order.company_id.id,})

                # ✅ Confirm and complete the move
                move._action_confirm()
                move._action_assign()
                move._action_done()

                move.sudo().write({'value': total_value, 'reference': order.name})
                new_moves.append(move.id)

            # ✅ Link back all moves (no duplicates)
            if new_moves:
                order.write({'move_ids': [(6, 0, list(set(order.move_ids.ids + new_moves)))]})

    def _create_return_move(self, move):
        """Create a reversal stock move for an already done move."""
        StockMove = self.env['stock.move']

        return_move = StockMove.create({
            'product_id': move.product_id.id,
            'product_uom': move.product_uom.id,
            'product_uom_qty': move.product_uom_qty,
            'price_unit': move.price_unit,
            'location_id': move.location_dest_id.id,  # reversed direction
            'location_dest_id': move.location_id.id,
            'origin': move.origin + ' (Return)',
            'reference': move.reference,
            'company_id': move.company_id.id,
            'partner_id': move.partner_id.id,
            'state': 'draft',
        })

        # Reverse stock quantities
        self.env['stock.move.line'].create({
            'move_id': return_move.id,
            'product_id': move.product_id.id,
            'product_uom_id': move.product_uom.id,
            'qty_done': move.product_uom_qty,
            'location_id': move.location_dest_id.id,
            'location_dest_id': move.location_id.id,
            'company_id': move.company_id.id,
        })

        # Confirm and complete return
        return_move._action_confirm()
        return_move._action_assign()
        return_move._action_done()

        return return_move

    def _recreate_journal_entry(self, order):
        """Recreate accounting entry for the purchase order and log chatter."""
        # Delete old move if exists
        if order.account_move_id:
            old_move = order.account_move_id
            old_move.button_draft()
            old_move.unlink()

        # Fallback accounts
        default_expense = self.env['account.account'].search([('account_type', '=', 'expense')], limit=1)
        default_payable = self.env['account.account'].search([('account_type', '=', 'liability_payable')], limit=1)
        journal = self.env['account.journal'].search([('type', '=', 'general')], limit=1)

        if not (default_expense and default_payable and journal):
            raise UserError(_("Please configure Expense, Payable accounts, and a General Journal."))

        # Build journal move
        move_lines = []
        for line in order.order_line.filtered('active'):
            move_lines.append((0, 0, {
                'name': f"{line.product_id.display_name} ({order.name})",
                'account_id': line.product_id.categ_id.property_account_expense_categ_id.id or default_expense.id,
                'debit': line.total,
                'credit': 0.0,
                'partner_id': line.vendor_id.id,

            }))

            move_lines.append((0, 0, {
                'name': order.vendor_id.name,
                'account_id': order.vendor_id.property_account_payable_id.id or default_payable.id,
                'credit': line.total,
                'debit': 0.0,
                'partner_id': line.vendor_id.id,

            }))

        move_vals = {
            'move_type': 'entry',
            'journal_id': journal.id,
            'date': fields.Date.context_today(self),
            'ref': f'Backorder Purchase: {order.name}',
            'line_ids': move_lines,
        }

        move = self.env['account.move'].create(move_vals)
        move.action_post()

        order.account_move_id = move

        # ✅ Post chatter message on the Journal Entry
        move.message_post(
            body=_(
                "Journal Entry created/updated via Backorder Purchase Update Wizard for order <b>%s</b>.") % order.name,
            subtype_xmlid="mail.mt_note"
        )

        # ✅ Also post to the related order chatter (if enabled)
        if hasattr(order, 'message_post'):
            order.message_post(
                body=_("New Journal Entry <b>%s</b> was created and linked.") % move.name,
                subtype_xmlid="mail.mt_note"
            )

    # ------------------------------------------------------------
    # Product cost updates
    # ------------------------------------------------------------
    def _update_stock_value(self, order, move):
        """Update product's last cost from new move."""
        for line in order.order_line.filtered('active'):
            if move.product_id == line.product_id:
                line.product_id.standard_price = line.price

    def _recompute_product_cost(self, product):
        """Recalculate average cost based on stock quant."""
        quant = self.env['stock.quant'].search([
            ('product_id', '=', product.id),
            ('location_id.usage', '=', 'internal')
        ], limit=1)
        if quant and quant.quantity:
            product.standard_price = quant.value / quant.quantity


# ------------------------------------------------------------
# Wizard Line Model
# ------------------------------------------------------------
class BackorderPurchaseUpdateWizardLine(models.TransientModel):
    _name = 'backorder.purchase.update.wizard.line'
    _description = 'Wizard Lines for Backorder Update'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    wizard_id = fields.Many2one('backorder.purchase.update.wizard', string='Wizard')
    line_id = fields.Many2one('backorder.purchase.order.line', string='Original Line')
    product_id = fields.Many2one('product.product', string='Product', required=True)
    vendor_id = fields.Many2one('res.partner', string='Vendor', required=True)
    quantity = fields.Float(string='Quantity', required=True)
    price = fields.Float(string='Unit Price', required=True)
    total = fields.Monetary(string='Total', compute='_compute_total', currency_field='currency_id', store=True)
    currency_id = fields.Many2one('res.currency', default=lambda self: self.env.company.currency_id)

    @api.depends('quantity', 'price')
    def _compute_total(self):
        for rec in self:
            rec.total = rec.quantity * rec.price

    @api.onchange('order_id')
    def _onchange_order_id_set_vendor(self):
        """Auto-set vendor from parent order when new line is created"""
        for line in self:
            if line.order_id and line.order_id.vendor_id:
                line.vendor_id = line.order_id.vendor_id
