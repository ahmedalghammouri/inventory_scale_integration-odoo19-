# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError

class TruckWeighing(models.Model):
    _inherit = 'truck.weighing'

    # Sales Links (stock fields inherited from purchase module)
    sale_order_id = fields.Many2one('sale.order', string='Sale Order', ondelete='restrict', tracking=True)
    sale_line_id = fields.Many2one('sale.order.line', string='Sale Order Line', ondelete='restrict')
    
    # Readonly flags for sales
    is_so_readonly = fields.Boolean(compute='_compute_so_readonly_flags')
    
    @api.depends('picking_id', 'sale_order_id')
    def _compute_so_readonly_flags(self):
        for record in self:
            record.is_so_readonly = bool(record.picking_id)



    def action_update_inventory(self):
        """ Update quantity in stock operation """
        self.ensure_one()
        
        if self.state != 'tare' or self.net_weight <= 0.0:
            raise UserError(_("Cannot update inventory. Net weight must be positive."))

        if not self.product_id:
            raise UserError(_("Product is required."))
        
        if self.picking_id:
            self._update_picking_quantity()
            self.message_post(body=_("Stock operation updated: %s KG of %s") % (self.net_weight, self.product_id.name))
        else:
            raise UserError(_("Please select a stock operation first."))
        
        self.state = 'done'
    
    def _update_picking_quantity(self):
        """ Update quantity in stock picking """
        move = self.picking_id.move_ids.filtered(lambda m: m.product_id == self.product_id)
        
        if not move:
            raise UserError(_("Product %s not found in stock operation.") % self.product_id.name)
        
        # Ensure picking is in correct state
        if self.picking_id.state == 'draft':
            self.picking_id.action_confirm()
        
        if self.picking_id.state in ['confirmed', 'waiting']:
            self.picking_id.action_assign()
        
        # Update quantity in move lines
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
        
        # Log the update
        demand_qty = move[0].product_uom_qty
        if self.net_weight > demand_qty:
            status = _("Over-delivery: +%s KG") % (self.net_weight - demand_qty)
        elif self.net_weight < demand_qty:
            status = _("Under-delivery: -%s KG") % (demand_qty - self.net_weight)
        else:
            status = _("Exact delivery")
        
        self.picking_id.message_post(
            body=_("Weighed: %s KG of %s (Demand: %s KG) - %s (from weighing %s)") % 
            (self.net_weight, self.product_id.name, demand_qty, status, self.name)
        )
    
    @api.onchange('picking_id')
    def _onchange_picking_id(self):
        if self.picking_id:
            self.partner_id = self.picking_id.partner_id
            self.location_dest_id = self.picking_id.location_dest_id
            weighable_moves = self.picking_id.move_ids.filtered(lambda m: m.product_id.is_weighable)
            if weighable_moves:
                move = weighable_moves[0]
                self.product_id = move.product_id
                # Auto-populate sale order from stock move  
                if hasattr(move, 'sale_line_id') and move.sale_line_id:
                    self.sale_line_id = move.sale_line_id
                    self.sale_order_id = move.sale_line_id.order_id
                # If no direct sale line, try to find from origin
                elif self.picking_id.origin:
                    so = self.env['sale.order'].search([('name', '=', self.picking_id.origin)], limit=1)
                    if so:
                        self.sale_order_id = so
                        # Find matching line
                        so_line = so.order_line.filtered(lambda l: l.product_id == move.product_id)
                        if so_line:
                            self.sale_line_id = so_line[0]

    def action_view_picking(self):
        self.ensure_one()
        return {
            'name': 'Stock Operation',
            'type': 'ir.actions.act_window',
            'res_model': 'stock.picking',
            'view_mode': 'form',
            'res_id': self.picking_id.id,
        }



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
                self.picking_id = existing_picking
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
        self.picking_id = picking
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
    
