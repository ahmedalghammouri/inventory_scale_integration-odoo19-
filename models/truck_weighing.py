# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)

class TruckWeighing(models.Model):
    _name = 'truck.weighing'
    _description = 'Truck Weighing Record (Gross/Tare)'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'weighing_date desc, id desc'

    name = fields.Char(string='Reference', required=True, copy=False, readonly=True, index=True, default=lambda self: _('New'))
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', string='Company', default=lambda self: self.env.company)
    
    # Scale Selection
    scale_id = fields.Many2one('weighing.scale', string='Weighing Scale', domain="[('is_enabled', '=', True), ('id', 'in', user_scale_ids)]", tracking=True)
    user_scale_ids = fields.Many2many('weighing.scale', compute='_compute_user_scales')
    
    # Truck & Material Info
    truck_id = fields.Many2one('truck.fleet', string='Truck', required=True, tracking=True)
    truck_plate = fields.Char(string='Plate Number', related='truck_id.plate_number', store=True, readonly=True)
    driver_name = fields.Char(string='Driver Name', related='truck_id.driver_name', readonly=False)
    
    # Purchase & Inventory Links
    partner_id = fields.Many2one('res.partner', string='Vendor', tracking=True)
    purchase_order_id = fields.Many2one('purchase.order', string='Purchase Order')
    purchase_line_id = fields.Many2one('purchase.order.line', string='Purchase Order Line')
    picking_id = fields.Many2one('stock.picking', string='Receipt', ondelete='restrict', tracking=True, help='One weighing record per receipt')
    product_id = fields.Many2one('product.product', string='Product', required=True, tracking=True)
    location_dest_id = fields.Many2one('stock.location', string='Destination Location')

    # Weight Fields
    live_weight = fields.Float(string='Live Weight (KG)', readonly=True)
    gross_weight = fields.Float(string='Gross Weight (KG)', tracking=True)
    tare_weight = fields.Float(string='Tare Weight (KG)', tracking=True)
    net_weight = fields.Float(string='Net Weight (KG)', compute='_compute_net_weight', store=True, tracking=True)
    
    # Dates
    weighing_date = fields.Datetime(string='Weighing Date', default=fields.Datetime.now, tracking=True)
    gross_date = fields.Datetime(string='Gross Weight Date', readonly=True)
    tare_date = fields.Datetime(string='Tare Weight Date', readonly=True)

    # State
    state = fields.Selection([
        ('draft', 'Draft'),
        ('gross', 'Gross Captured'),
        ('tare', 'Tare Captured'),
        ('done', 'Done'),
        ('cancel', 'Cancelled')
    ], string='Status', default='draft', tracking=True)
    
    # Notes
    notes = fields.Text(string='Notes')
    
    @api.model
    def get_dashboard_data(self):
        """ Get statistics for dashboard """
        today = fields.Date.today()
        return {
            'draft_count': self.search_count([('state', '=', 'draft')]),
            'gross_count': self.search_count([('state', '=', 'gross')]),
            'tare_count': self.search_count([('state', '=', 'tare')]),
            'done_today': self.search_count([('state', '=', 'done'), ('weighing_date', '>=', today)]),
            'total_weight_today': sum(self.search([('state', '=', 'done'), ('weighing_date', '>=', today)]).mapped('net_weight')),
        }

    @api.depends('gross_weight', 'tare_weight')
    def _compute_net_weight(self):
        """ Calculate the Net Weight (Gross - Tare) """
        for record in self:
            if record.gross_weight > 0 and record.tare_weight > 0:
                record.net_weight = record.gross_weight - record.tare_weight
            else:
                record.net_weight = 0.0
    
    @api.depends('create_uid')
    def _compute_user_scales(self):
        """ Get scales assigned to current user """
        for record in self:
            user = record.create_uid or self.env.user
            record.user_scale_ids = user.assigned_scale_ids
    
    @api.onchange('user_scale_ids')
    def _onchange_user_scale_ids(self):
        """ Auto-select first assigned scale if no scale selected """
        if self.user_scale_ids and not self.scale_id:
            self.scale_id = self.user_scale_ids[0]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('truck.weighing.sequence') or _('New')
            # Set default scale from user if not specified
            if not vals.get('scale_id'):
                if self.env.user.default_scale_id:
                    vals['scale_id'] = self.env.user.default_scale_id.id
                elif self.env.user.assigned_scale_ids:
                    vals['scale_id'] = self.env.user.assigned_scale_ids[0].id
        return super(TruckWeighing, self).create(vals_list)

    def action_fetch_live_weight(self):
        """ Fetch current weight from scale without changing state """
        self.ensure_one()
        if not self.scale_id:
            raise UserError(_("Please select a weighing scale first."))
        
        try:
            weight = self.scale_id.get_weight()
            self.live_weight = weight
            self.message_post(body=_("Live weight fetched from %s: %s KG") % (self.scale_id.name, self.live_weight))
        except Exception as e:
            raise UserError(_("Error: %s") % str(e))

    def action_set_gross_from_live(self):
        """ Set gross weight from live weight and change state """
        self.ensure_one()
        if self.live_weight > 0:
            self.gross_weight = self.live_weight
            self.gross_date = fields.Datetime.now()
            self.state = 'gross'
            self.message_post(body=_("Gross weight set: %s KG") % self.gross_weight)
        else:
            raise UserError(_("Please fetch live weight first."))

    def action_set_tare_from_live(self):
        """ Set tare weight from live weight and change state """
        self.ensure_one()
        if self.live_weight > 0:
            if self.live_weight >= self.gross_weight:
                raise UserError(_("Tare weight must be less than gross weight."))
            self.tare_weight = self.live_weight
            self.tare_date = fields.Datetime.now()
            self.state = 'tare'
            self.message_post(body=_("Tare weight set: %s KG") % self.tare_weight)
        else:
            raise UserError(_("Please fetch live weight first."))

    def action_update_inventory(self):
        """ Update received quantity in receipt without validation """
        self.ensure_one()
        
        if self.state != 'tare' or self.net_weight <= 0.0:
            raise UserError(_("Cannot update inventory. Net weight must be positive."))

        if not self.product_id:
            raise UserError(_("Product is required."))
        
        if not self.picking_id:
            raise UserError(_("Please select a receipt first."))

        # Update the done quantity in the receipt without validating
        self._update_receipt_quantity()
        
        self.state = 'done'
        self.message_post(body=_("Receipt updated: %s KG of %s") % (self.net_weight, self.product_id.name))
    
    def _update_receipt_quantity(self):
        """ Update received quantity only (not demand) in receipt without validation """
        # Find the move for this product
        move = self.picking_id.move_ids.filtered(lambda m: m.product_id == self.product_id)
        
        if not move:
            raise UserError(_("Product %s not found in receipt.") % self.product_id.name)
        
        # Ensure picking is in correct state
        if self.picking_id.state == 'draft':
            self.picking_id.action_confirm()
        
        if self.picking_id.state in ['confirmed', 'waiting']:
            self.picking_id.action_assign()
        
        # Update ONLY received quantity in move lines (not demand)
        if move[0].move_line_ids:
            for ml in move[0].move_line_ids:
                ml.quantity = self.net_weight
        else:
            # Create move line if doesn't exist
            self.env['stock.move.line'].create({
                'move_id': move[0].id,
                'product_id': self.product_id.id,
                'product_uom_id': self.product_id.uom_id.id,
                'location_id': self.picking_id.location_id.id,
                'location_dest_id': self.picking_id.location_dest_id.id,
                'quantity': self.net_weight,
                'picking_id': self.picking_id.id,
            })
        
        # Log the update with comparison to demand
        demand_qty = move[0].product_uom_qty
        if self.net_weight > demand_qty:
            status = _("Over-delivery: +%s KG") % (self.net_weight - demand_qty)
        elif self.net_weight < demand_qty:
            status = _("Under-delivery: -%s KG") % (demand_qty - self.net_weight)
        else:
            status = _("Exact delivery")
        
        self.picking_id.message_post(
            body=_("Received: %s KG of %s (Demand: %s KG) - %s (from weighing %s)") % 
            (self.net_weight, self.product_id.name, demand_qty, status, self.name)
        )
    
    @api.onchange('picking_id')
    def _onchange_picking_id(self):
        if self.picking_id:
            self.partner_id = self.picking_id.partner_id
            self.location_dest_id = self.picking_id.location_dest_id
            weighable_moves = self.picking_id.move_ids.filtered(lambda m: m.product_id.is_weighable)
            if weighable_moves:
                self.product_id = weighable_moves[0].product_id
                if weighable_moves[0].purchase_line_id:
                    self.purchase_line_id = weighable_moves[0].purchase_line_id
                    self.purchase_order_id = self.purchase_line_id.order_id
    
    @api.onchange('purchase_order_id')
    def _onchange_purchase_order_id(self):
        if self.purchase_order_id:
            self.partner_id = self.purchase_order_id.partner_id
            # Get first weighable line with remaining quantity
            for line in self.purchase_order_id.order_line.filtered(lambda l: l.product_id.is_weighable):
                remaining_qty = line.product_qty - line.qty_received
                if remaining_qty > 0:
                    self.purchase_line_id = line
                    self.product_id = line.product_id
                    break
            
            # Try to find or create draft receipt for this PO
            existing_picking = self.env['stock.picking'].search([
                ('origin', '=', self.purchase_order_id.name),
                ('state', 'in', ['draft', 'waiting', 'confirmed', 'assigned']),
                ('picking_type_code', '=', 'incoming')
            ], limit=1)
            
            if existing_picking:
                self.picking_id = existing_picking
            else:
                # Create new draft receipt
                self._create_draft_receipt_from_po()
    
    @api.onchange('purchase_line_id')
    def _onchange_purchase_line_id(self):
        if self.purchase_line_id:
            self.product_id = self.purchase_line_id.product_id
            self.purchase_order_id = self.purchase_line_id.order_id
    
    def _create_draft_receipt_from_po(self):
        """ Create draft receipt from purchase order """
        if not self.purchase_order_id:
            return
        
        picking_type = self.env['stock.picking.type'].search([
            ('code', '=', 'incoming'),
            ('company_id', '=', self.company_id.id)
        ], limit=1)
        
        if not picking_type:
            return
        
        location_dest = picking_type.default_location_dest_id
        
        picking = self.env['stock.picking'].create({
            'partner_id': self.partner_id.id,
            'picking_type_id': picking_type.id,
            'location_id': picking_type.default_location_src_id.id,
            'location_dest_id': location_dest.id,
            'origin': self.purchase_order_id.name,
        })
        
        # Create moves for lines with remaining quantity
        for line in self.purchase_order_id.order_line:
            remaining_qty = line.product_qty - line.qty_received
            if remaining_qty > 0:
                self.env['stock.move'].create({
                    'product_id': line.product_id.id,
                    'product_uom_qty': remaining_qty,
                    'product_uom': line.product_id.uom_id.id,
                    'picking_id': picking.id,
                    'location_id': picking_type.default_location_src_id.id,
                    'location_dest_id': location_dest.id,
                    'purchase_line_id': line.id,
                })
        
        picking.action_confirm()
        self.picking_id = picking
        self.location_dest_id = location_dest
    
    @api.onchange('truck_id')
    def _onchange_truck_id(self):
        if self.truck_id:
            self.driver_name = self.truck_id.driver_name
    
    def action_view_purchase_order(self):
        self.ensure_one()
        return {
            'name': 'Purchase Order',
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.order',
            'view_mode': 'form',
            'res_id': self.purchase_order_id.id,
        }
    
    def action_view_picking(self):
        self.ensure_one()
        return {
            'name': 'Receipt',
            'type': 'ir.actions.act_window',
            'res_model': 'stock.picking',
            'view_mode': 'form',
            'res_id': self.picking_id.id,
        }


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'
    
    weighing_count = fields.Integer(string='Weighing Records', compute='_compute_weighing_data', store=True)
    total_net_weight = fields.Float(string='Total Received Weight (KG)', compute='_compute_weighing_data', store=True)
    has_weighable_products = fields.Boolean(string='Has Weighable Products', compute='_compute_has_weighable_products')
    
    @api.depends('order_line.weighing_ids.net_weight', 'order_line.weighing_ids.state')
    def _compute_weighing_data(self):
        for order in self:
            weighings = self.env['truck.weighing'].search([('purchase_order_id', '=', order.id), ('state', '=', 'done')])
            order.weighing_count = len(weighings)
            order.total_net_weight = sum(weighings.mapped('net_weight'))
    
    @api.depends('order_line.product_id.is_weighable')
    def _compute_has_weighable_products(self):
        for order in self:
            order.has_weighable_products = any(line.product_id.is_weighable for line in order.order_line)
    
    def action_view_weighing_records(self):
        return {
            'name': 'Weighing Records',
            'type': 'ir.actions.act_window',
            'res_model': 'truck.weighing',
            'view_mode': 'list,form',
            'domain': [('purchase_order_id', '=', self.id)],
            'context': {'default_purchase_order_id': self.id, 'default_partner_id': self.partner_id.id}
        }


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'
    
    weighing_ids = fields.One2many('truck.weighing', 'purchase_line_id', string='Weighing Records')
    total_received_weight = fields.Float(string='Total Received (KG)', compute='_compute_total_received_weight', store=True)
    
    @api.depends('weighing_ids.net_weight', 'weighing_ids.state')
    def _compute_total_received_weight(self):
        for line in self:
            done_weighings = line.weighing_ids.filtered(lambda w: w.state == 'done')
            line.total_received_weight = sum(done_weighings.mapped('net_weight'))


class StockPicking(models.Model):
    _inherit = 'stock.picking'
    
    weighing_count = fields.Integer(string='Weighing Records', compute='_compute_weighing_count')
    has_weighable_products = fields.Boolean(string='Has Weighable Products', compute='_compute_has_weighable_products')
    
    def _compute_weighing_count(self):
        for picking in self:
            picking.weighing_count = self.env['truck.weighing'].search_count([('picking_id', '=', picking.id)])
    
    @api.depends('move_ids.product_id.is_weighable')
    def _compute_has_weighable_products(self):
        for picking in self:
            picking.has_weighable_products = any(move.product_id.is_weighable for move in picking.move_ids)
    
    def action_view_weighing_records(self):
        return {
            'name': 'Weighing Records',
            'type': 'ir.actions.act_window',
            'res_model': 'truck.weighing',
            'view_mode': 'list,form',
            'domain': [('picking_id', '=', self.id)],
            'context': {'default_picking_id': self.id, 'default_partner_id': self.partner_id.id}
        }