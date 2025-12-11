# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError

class TruckWeighing(models.Model):
    _inherit = 'truck.weighing'

    # Purchase Links
    purchase_order_id = fields.Many2one('purchase.order', string='Purchase Order')
    purchase_line_id = fields.Many2one('purchase.order.line', string='Purchase Order Line')

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

    def action_view_purchase_order(self):
        self.ensure_one()
        return {
            'name': 'Purchase Order',
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.order',
            'view_mode': 'form',
            'res_id': self.purchase_order_id.id,
        }