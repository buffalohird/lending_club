import warnings
from collections import defaultdict
from exceptions import ZeroDivisionError

import pandas as pd
import numpy as np

from loan import Loan
from investor import Investor

class Backtest():
    def __init__(self, sdate, edate, buy_solver, db, cash=1000, buy_size=25.0, liquidity_limit=1.0):
        self.investor = Investor(cash)
        self.month = pd.Period(sdate, freq='M')
        self.end_month = pd.Period(edate, freq='M')
        self.buy_solver = buy_solver
        self.db = db
        self.buy_size = buy_size
        self.liquidity_limit = liquidity_limit
        
        self.stats = defaultdict(dict)
        self.loans = pd.DataFrame()
        self.current_loans = dict()
    
    def solve_month(self):
        self.investor.get_payments()
        new_loans, matching_new_loans, available_new_loans = self.buy()
        
        self.loans = pd.concat([self.loans, new_loans], axis=0)
        self.current_loans[self.month] = pd.DataFrame([loan.to_dict() for loan in self.investor.loans])
        self.stats['loans added'][self.month] = new_loans.shape[0]
        self.stats['strategy available loans'][self.month] = matching_new_loans
        self.stats['available loans'][self.month] = available_new_loans
        self.stats['loans held'][self.month] = len(self.investor.loans)
        self.stats['cumulative loans held'][self.month] = self.loans.shape[0]
        self.stats['cumulative defaults'][self.month] = self.investor.cum_defaults
        self.stats['cash held'][self.month] = self.investor.balance
        self.stats['net worth'][self.month] = self.investor.get_net_worth()
        self.stats['imbalance'][self.month] = self.investor.cum_imbalance
        self.stats['abs imbalance'][self.month] = self.investor.abs_cum_imbalance
        self.stats['imbalance %'][self.month] = self.stats['imbalance'][self.month] / self.stats['net worth'][self.month]
        self.stats['abs imbalance %'][self.month] = self.stats['abs imbalance'][self.month] / self.stats['net worth'][self.month]
        
        self.month += 1
        
    def buy(self):
        month_db = self.db[self.db['issue_d'] == self.month]
        purchase_count = np.floor(self.investor.balance / self.buy_size)
        # if purchase_count > 0: ### We can just pass 0 to the solver and get back an empty dataframe for now
        buy_dict = self.buy_solver(self.month, self.investor, month_db, purchase_count, self.liquidity_limit)

        buy_df = buy_dict['loans']
        buy_matching = buy_dict['matching quantity']
        buy_available = buy_dict['available quantity']

        if buy_df.empty:
            buy_df = pd.DataFrame()

        new_loans = buy_df.apply(self.map_loan_row, axis=1)

        self.investor.buy_loans(new_loans)
        return new_loans, buy_matching, buy_available   

    
    def map_loan_row(self, row):
        # print '####', row['recoveries']
        return Loan(
            loan_id=row['id'],
            grade=row['grade'],
            int_rate=row['int_rate'],
            term = row['term'],
            amount=row['funded_amnt'],
            issue_date=row['issue_d'],
            last_date=row['last_pymnt_d'],
            investment=self.buy_size,
            defaults=row['defaulted'],
            total_payment=row['total_pymnt'],
            total_principle=row['total_rec_prncp'],
            recoveries=row['recoveries']
        )
        
    def run(self):
        while self.month <= self.end_month:
            print self.month, self.end_month
            self.solve_month()
            
        self.stats = pd.DataFrame(self.stats)
        self.stats['defaults'] = self.stats['cumulative defaults'].diff()
        self.stats['monthly return'] = self.stats['net worth'].diff().shift(-1) / self.stats['net worth']
        self.stats['annualized return'] = self.stats['monthly return'].resample('A', how='mean').resample('M', fill_method='ffill')
        self.stats['realized liquidity'] = self.stats['loans added'] / self.stats['available loans']
        self.stats['realized vs strategy liquidity'] = self.stats['loans added'] / self.stats['strategy available loans'].replace(0, np.nan)
        self.stats['strategy liquidity'] = self.stats['strategy available loans'] / self.stats['available loans'].replace(0, np.nan)
        self.stats['default rate'] = self.stats['defaults'] / self.stats['loans held'].replace(0, np.nan)
        self.stats['growth of $1'] = self.stats['net worth'] / self.stats['net worth'].iloc[0]
        
        self.stats_dict = dict()

        try:
            self.stats_dict['sharpe'] = self.stats['net worth'].diff().mean() / self.stats['net worth'].diff().std() * np.sqrt(12)
        except ZeroDivisionError as e:
            warnings.warn('Division by zero: Sharpe Ratio')
            self.stats_dict['sharpe'] = np.nan

        self.stats_dict = pd.Series(self.stats_dict)

        self.loan_stats = dict()
        self.loan_stats['grade'] = pd.DataFrame({month: self.current_loans[month]['grade'].value_counts() for month in self.current_loans if not self.current_loans[month].empty}).T.reindex(self.stats.index)
        self.loan_stats['duration'] = pd.DataFrame({month: (self.current_loans[month]['end_date'] - month) for month in self.current_loans if not self.current_loans[month].empty}).T.reindex(self.stats.index) / 12
        self.loan_stats['int_rate'] = pd.DataFrame({month: self.current_loans[month]['int_rate'] for month in self.current_loans if not self.current_loans[month].empty}).T.reindex(self.stats.index)
        self.loan_stats['defaulted'] = pd.DataFrame({month: self.current_loans[month]['defaulted'] for month in self.current_loans if not self.current_loans[month].empty}).T.reindex(self.stats.index)
        self.loan_stats['remaining_amount'] = pd.DataFrame({month: self.current_loans[month]['remaining_amount'] for month in self.current_loans if not self.current_loans[month].empty}).T.reindex(self.stats.index)

        # TODO: add imbalance stats ya dickhead

        self.loan_stats_total = dict()
        for category in ['duration', 'int_rate']: # self.loan_stats:
            self.loan_stats_total[category] = pd.Series(self.loan_stats[category].values.flatten()).dropna()
        
        self.loan_stats_total['grade'] = self.loan_stats['grade'].sum()

            
        return self.stats

    def generate_report(self):
        pass



def simple_filter_buy_solver(month, investor, month_db, number, liquidity_limit):
    available_quantity = month_db.shape[0]
    number = min(number, int(np.floor(liquidity_limit * available_quantity)))

    return_df = month_db[month_db['emp_length'] > 5.0]
    return_dict = {
        'loans': return_df.iloc[0:number],
        'matching quantity': return_df.shape[0],
        'available quantity': available_quantity
    }
    return return_dict

def generic_buy_solver(month, investor, month_db, number, liquidity_limit):
    '''
    :param month: pandas.Period, the month to be solved for
    :param investor: lending_club.com.Investor, an investor instance (investor.loans contains the currently held loans) 
    :param month_db: pandas.DataFrame, a dataframe of all loans available for the given month
    :param number: int, the desired number of loans to be bought. The solver should return at most this number of loans

    returns: return_dict{
        loans: pandas.DataFrame, subset of month_db of the loans to be purchased
        matching: int, the number of loans available matching this strategy in month_db
        available: int, the number of loans available in month_db
    }
    '''
    matching_quantity = month_db.shape[0]
    available_quantity = month_db.shape[0]
    print 'available ', available_quantity
    print 'solver number ', number
    number = min(number, int(np.floor(liquidity_limit * available_quantity)))
    print 'solver number restricted ', number
    return_dict = {
        'loans': month_db.iloc[0:number],
        'matching quantity': matching_quantity,
        'available quantity': available_quantity
    }
    return return_dict


def single_buy_solver(month, investor, month_db, number, liquidity_limit):
    buy_number = min(number, 1)
    loan_df = month_db.iloc[0:buy_number]
    return_dict = {
        'loans': loan_df,
        'matching quantity': buy_number,
        'available quantity': month_db.shape[0]
    }
    return return_dict


def zero_buy_solver(month, investor, month_db, number, liqudity_limit):
    buy_number = 0
    return_dict = {
        'loans': month_db.iloc[0:0],
        'matching quantity': buy_number,
        'available quantity': month_db.shape[0]
    }
    return return_dict
    
            