# -*- encoding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#
#    Copyright (C) 2010-2012 OpenERP s.a. (<http://openerp.com>).
#    Copyright (c) 2014 Noviat nv/sa (www.noviat.com). All rights reserved.
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program. If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################

from openerp.osv import fields, orm
from openerp.tools.translate import _
from dateutil.relativedelta import relativedelta
from datetime import datetime
import logging
_logger = logging.getLogger(__name__)


class account_asset_remove(orm.TransientModel):
    _name = 'account.asset.remove'
    _description = 'Remove Asset'

    _columns = {
        'date_remove': fields.date('Asset Removal Date', required=True,
            help="Removal date must be right after the last posted entry "
                 "in case of early removal"),
        'period_id': fields.many2one(
            'account.period', 'Force Period',
            domain=[('state', '<>', 'done')],
            help="Keep empty to use the period of the removal ate."),
        'note': fields.text('Notes'),
    }

    def remove(self, cr, uid, ids, context=None):
        asset_obj = self.pool.get('account.asset.asset')
        asset_line_obj = self.pool.get('account.asset.depreciation.line')
        move_line_obj = self.pool.get('account.move.line')
        move_obj = self.pool.get('account.move')
        period_obj = self.pool.get('account.period')

        if context.get('early_removal', False):
            residual_value = self.prepare_early_removal(cr, uid, ids,
                                                        context=context)

        wiz_data = self.browse(cr, uid, ids[0], context=context)
        asset_id = context['active_id']
        asset = asset_obj.browse(cr, uid, asset_id, context=context)
        categ = asset.category_id
        ctx = dict(context, company_id=asset.company_id.id)
        period_id = wiz_data.period_id and wiz_data.period_id.id or False
        if not period_id:
            ctx.update(account_period_prefer_normal=True)
            period_ids = period_obj.find(
                cr, uid, wiz_data.date_remove, context=ctx)
            period_id = period_ids[0]
        dl_ids = asset_line_obj.search(
            cr, uid,
            [('asset_id', '=', asset.id), ('type', '=', 'depreciate')],
            order='line_date desc')
        last_date = asset_line_obj.browse(cr, uid, dl_ids[0]).line_date
        if wiz_data.date_remove < last_date:
            raise orm.except_orm(
                _('Error!'),
                _("The removal date must be after "
                  "the last depreciation date."))

        line_name = asset_obj._get_depreciation_entry_name(
            cr, uid, asset, len(dl_ids) + 1, context=context)
        journal_id = asset.category_id.journal_id.id

        # create move
        move_vals = {
            'name': asset.name,
            'date': wiz_data.date_remove,
            'ref': line_name,
            'period_id': period_id,
            'journal_id': journal_id,
            'narration': wiz_data.note,
            }
        move_id = move_obj.create(cr, uid, move_vals, context=context)
        partner_id = asset.partner_id and asset.partner_id.id or False
        depr_amount = asset.asset_value - residual_value
        move_line_obj.create(cr, uid, {
            'name': asset.name,
            'ref': line_name,
            'move_id': move_id,
            'account_id': categ.account_depreciation_id.id,
            'debit': depr_amount > 0 and depr_amount or 0.0,
            'credit': depr_amount < 0 and -depr_amount or 0.0,
            'period_id': period_id,
            'journal_id': journal_id,
            'partner_id': partner_id,
            'date': wiz_data.date_remove,
            'asset_id': asset.id
        }, context={'allow_asset': True})
        move_line_obj.create(cr, uid, {
            'name': asset.name,
            'ref': line_name,
            'move_id': move_id,
            'account_id': categ.account_asset_id.id,
            'debit': asset.asset_value < 0 and -asset.asset_value or 0.0,
            'credit': asset.asset_value > 0 and asset.asset_value or 0.0,
            'period_id': period_id,
            'journal_id': journal_id,
            'partner_id': partner_id,
            'date': wiz_data.date_remove,
            'asset_id': asset.id
        }, context={'allow_asset': True})

        if context.get('early_removal', False):
            invoice_line_obj = self.pool['account.invoice.line']
            inv_line_id = invoice_line_obj.search(cr, uid,
                                                 [('asset_id', '=', asset.id)],
                                                 context=context)
            if inv_line_id:
                if len(inv_line_id) > 1:
                    raise orm.except_orm(
                        _('Error!'),
                        _("More than 1 invoice match this asset"))
                inv_line = invoice_line_obj.browse(cr, uid, inv_line_id, context=context)[0]
                account_sale_id = inv_line.account_id
                line_amount = inv_line.price_subtotal
                residual_value = residual_value - line_amount
                move_line_obj.create(cr, uid, {
                    'name': inv_line,
                    'ref': line_name,
                    'move_id': move_id,
                    'account_id': inv_line.account_id.id,
                    'debit': line_amount > 0 and line_amount or 0.0,
                    'credit': line_amount < 0 and -line_amount or 0.0,
                    'period_id': period_id,
                    'journal_id': journal_id,
                    'partner_id': inv_line.invoice_id.partner_id.id,
                    'date': wiz_data.date_remove,
                    'asset_id': asset.id
                }, context={'allow_asset': True})
                if residual_value < 0:
                    residual_account = categ.account_plus_value_asset_id
                    if not residual_account:
                        raise orm.except_orm(
                        _('Error!'),
                        _("You have to configure the plus-value Account "
                          "in the asset category"))
                    
                elif residual_value > 0:
                    residual_account = categ.account_residual_asset_value_id
            else:
                residual_account = categ.account_residual_asset_value_id
            move_line_obj.create(cr, uid, {
                'name': asset.name,
                'ref': line_name,
                'move_id': move_id,
                'account_id': residual_account.id,
                'debit': residual_value > 0 and residual_value or 0.0,
                'credit': residual_value < 0 and -residual_value or 0.0,
                'period_id': period_id,
                'journal_id': journal_id,
                'partner_id': partner_id,
                'date': wiz_data.date_remove,
                'asset_id': asset.id
            }, context={'allow_asset': True})

        # create asset line
        asset_line_vals = {
            'amount': depr_amount,
            'asset_id': asset_id,
            'name': line_name,
            'line_date': wiz_data.date_remove,
            'move_id': move_id,
            'type': 'remove',
        }
        asset_line_obj.create(cr, uid, asset_line_vals, context=context)
        asset.write({'state': 'removed', 'date_remove': wiz_data.date_remove})

        return {'type': 'ir.actions.act_window_close'}

    def prepare_early_removal(self, cr, uid, ids, context=None):
        asset_obj = self.pool.get('account.asset.asset')
        asset_line_obj = self.pool.get('account.asset.depreciation.line')
        move_line_obj = self.pool.get('account.move.line')
        move_obj = self.pool.get('account.move')
        period_obj = self.pool.get('account.period')

        wiz_data = self.browse(cr, uid, ids[0], context=context)
        asset_id = context['active_id']
        asset = asset_obj.browse(cr, uid, asset_id, context=context)
        if not asset.category_id.account_residual_asset_value_id:
            raise orm.except_orm(
                _('Error!'),
                _("You have to configure the Residual Value Account "
                  "in the asset category"))

        dl_ids = asset_line_obj.search(
            cr, uid,
            [('asset_id', '=', asset.id), ('type', '=', 'depreciate'),
             ('init_entry', '=', False), ('move_check', '=', False)],
            order='line_date asc')
        first_to_depreciate_dl = asset_line_obj.browse(cr, uid, dl_ids[0])

        first_date = first_to_depreciate_dl.line_date
        if wiz_data.date_remove > first_date:
            raise orm.except_orm(
                _('Error!'),
                _("You can't make an early removal if all the depreciation "
                  "lines for previous periods are not posted."))

        last_depr_date = first_to_depreciate_dl.previous_id.line_date
        if wiz_data.date_remove < last_depr_date:
            raise orm.except_orm(
                _('Error!'),
                _("The removal date must be after "
                  "the last posted depreciation date."))

        if asset.prorata: 
            period_number_days = (datetime.strptime(first_date, '%Y-%m-%d') - \
                datetime.strptime(last_depr_date, '%Y-%m-%d')).days
            date_remove = datetime.strptime(wiz_data.date_remove,'%Y-%m-%d')
            new_line_date = date_remove + relativedelta(days=-1)
            to_depreciate_days = (new_line_date - \
                datetime.strptime(last_depr_date, '%Y-%m-%d')).days
            to_depreciate_amount = float(to_depreciate_days)/ \
                float(period_number_days) * first_to_depreciate_dl.amount
            rest_period = first_to_depreciate_dl.amount - to_depreciate_amount
            remaining_to_depreciate_dl = asset_line_obj.browse(cr, uid,
                                                               dl_ids[1:])
            remaining_tot = sum([l.amount for l in remaining_to_depreciate_dl])
            residual_value = remaining_tot + rest_period

            update_vals = {
                'amount': to_depreciate_amount,
                'line_date': new_line_date
            }
            first_to_depreciate_dl.write(update_vals)
            asset_line_obj.create_move(cr, uid, [dl_ids[0]],
                                       context=context)
            dl_ids.pop(0)
        else:
            residual_value = first_to_depreciate_dl.previous_id.remaining_value

        asset_line_obj.unlink(cr, uid, dl_ids, context=context)     
        

        return residual_value

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
