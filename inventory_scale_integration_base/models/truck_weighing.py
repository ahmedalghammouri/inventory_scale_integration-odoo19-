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
    
    # Basic Links
    product_id = fields.Many2one('product.product', string='Product', required=True, tracking=True)

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
            # In base module, show all enabled scales
            record.user_scale_ids = self.env['weighing.scale'].search([('is_enabled', '=', True)])
    
    @api.onchange('user_scale_ids')
    def _onchange_user_scale_ids(self):
        """ Auto-select first available scale if no scale selected """
        if self.user_scale_ids and not self.scale_id:
            self.scale_id = self.user_scale_ids[0]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('truck.weighing.sequence') or _('New')
            # Set default scale if not specified
            if not vals.get('scale_id'):
                enabled_scales = self.env['weighing.scale'].search([('is_enabled', '=', True)], limit=1)
                if enabled_scales:
                    vals['scale_id'] = enabled_scales[0].id
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

    def action_complete_weighing(self):
        """ Complete weighing process """
        self.ensure_one()
        
        if self.state != 'tare' or self.net_weight <= 0.0:
            raise UserError(_("Cannot complete weighing. Net weight must be positive."))

        if not self.product_id:
            raise UserError(_("Product is required."))
        
        self.message_post(body=_("Weighing completed: %s KG of %s") % (self.net_weight, self.product_id.name))
        self.state = 'done'
    
    @api.onchange('truck_id')
    def _onchange_truck_id(self):
        if self.truck_id:
            self.driver_name = self.truck_id.driver_name
    
