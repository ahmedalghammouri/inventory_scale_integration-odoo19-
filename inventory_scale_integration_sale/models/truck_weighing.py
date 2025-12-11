# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError

class TruckWeighing(models.Model):
    _inherit = 'truck.weighing'

    # Sales & Delivery Links
    sale_order_id = fields.Many2one('sale.order', string='Sale Order', ondelete='restrict', tracking=True)
    sale_line_id = fields.Many2one('sale.order.line', string='Sale Order Line', ondelete='restrict')
    delivery_id = fields.Many2one('stock.picking', string='Delivery', ondelete='restrict', tracking=True, help='One weighing record per delivery', domain="[('picking_type_code', '=', 'outgoing')]")

    @api.onchange('delivery_id')
    def _onchange_delivery_id(self):
        if self.delivery_id:
            self.partner_id = self.delivery_id.partner_id
            self.location_dest_id = self.delivery_id.location_dest_id
            weighable_moves = self.delivery_id.move_ids.filtered(lambda m: m.product_id.is_weighable)
            if weighable_moves:
                self.product_id = weighable_moves[0].product_id
                if weighable_moves[0].sale_line_id:
                    self.sale_line_id = weighable_moves[0].sale_line_id
                    self.sale_order_id = self.sale_line_id.order_id

    @api.onchange('sale_order_id')
    def _onchange_sale_order_id(self):
        if self.sale_order_id:
            self.partner_id = self.sale_order_id.partner_id
            # Get first weighable line with remaining quantity
            for line in self.sale_order_id.order_line.filtered(lambda l: l.product_id.is_weighable):
                remaining_qty = line.product_uom_qty - line.qty_delivered
                if remaining_qty > 0:
                    self.sale_line_id = line
                    self.product_id = line.product_id
                    break
            
            # Try to find or create draft delivery for this SO
            existing_picking = self.env['stock.picking'].search([
                ('origin', '=', self.sale_order_id.name),
                ('state', 'in', ['draft', 'waiting', 'confirmed', 'assigned']),
                ('picking_type_code', '=', 'outgoing')
            ], limit=1)
            
            if existing_picking:
                self.delivery_id = existing_picking
            else:
                # Create new draft delivery
                self._create_draft_delivery_from_so()

    @api.onchange('sale_line_id')
    def _onchange_sale_line_id(self):
        if self.sale_line_id:
            self.product_id = self.sale_line_id.product_id
            self.sale_order_id = self.sale_line_id.order_id

    def _create_draft_delivery_from_so(self):
        """ Create draft delivery from sale order """
        if not self.sale_order_id:
            return
        
        picking_type = self.env['stock.picking.type'].search([
            ('code', '=', 'outgoing'),
            ('company_id', '=', self.company_id.id)
        ], limit=1)
        
        if not picking_type:
            return
        
        location_dest = self.sale_order_id.partner_id.property_stock_customer
        
        picking = self.env['stock.picking'].create({
            'partner_id': self.partner_id.id,
            'picking_type_id': picking_type.id,
            'location_id': picking_type.default_location_src_id.id,
            'location_dest_id': location_dest.id,
            'origin': self.sale_order_id.name,
        })
        
        # Create moves for lines with remaining quantity
        for line in self.sale_order_id.order_line:
            remaining_qty = line.product_uom_qty - line.qty_delivered
            if remaining_qty > 0:
                self.env['stock.move'].create({
                    'product_id': line.product_id.id,
                    'product_uom_qty': remaining_qty,
                    'product_uom': line.product_id.uom_id.id,
                    'picking_id': picking.id,
                    'location_id': picking_type.default_location_src_id.id,
                    'location_dest_id': location_dest.id,
                    'sale_line_id': line.id,
                })
        
        picking.action_confirm()
        self.delivery_id = picking
        self.location_dest_id = location_dest

    def action_view_sale_order(self):
        self.ensure_one()
        return {
            'name': 'Sale Order',
            'type': 'ir.actions.act_window',
            'res_model': 'sale.order',
            'view_mode': 'form',
            'res_id': self.sale_order_id.id,
        }
    
    def action_view_delivery(self):
        self.ensure_one()
        return {
            'name': 'Delivery',
            'type': 'ir.actions.act_window',
            'res_model': 'stock.picking',
            'view_mode': 'form',
            'res_id': self.delivery_id.id,
        }