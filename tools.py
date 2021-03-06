import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import scipy as sp
from scipy import stats
from scipy.optimize import minimize
from sklearn.linear_model import Lasso, LassoLarsIC


def make_stats_maxence(df_price: pd.DataFrame):
    df_return = df_price.pct_change().dropna()
    stats.describe(df_return)
    t_tstat, p_tstat = stats.ttest_rel(df_return.iloc[:,0], df_return.iloc[:, 1])  # T-test
    t_KS, p_KS = stats.ks_2samp(df_return.iloc[:,0], df_return.iloc[:, 1])  # KS -> p petit pas la meme distri
    tau, p_tau = stats.kendalltau(df_return.iloc[:,0], df_return.iloc[:, 1])  # Tau de Kendall

    return stats.describe(df_return), "t test: t = %g  p = %g" % (t_tstat, p_tstat), \
        "KS test: t = %g  p = %g" % (t_KS, p_KS), "KendallTau: t = %g  p = %g" % (tau, p_tau)


def replication_stats(df_price: pd.DataFrame, fund_name: str):

    rho = df_price.pct_change().corr(method="pearson")
    tau = df_price.pct_change().corr(method="kendall")
    returns_track = df_price.pct_change().dropna()
    returns_fund = df_price[fund_name].pct_change().dropna()

    df = pd.DataFrame()
    df['Tracking error'] = (returns_track.T - returns_fund.values).std(axis=1)
    df['R-squared'] = 1 - (returns_track.T - returns_fund.values).var(axis=1) / returns_fund.values.var()
    df['Sharpe ratio'] = np.sqrt(252) * returns_track.mean() / returns_track.std()
    df['Annual Return'] = (df_price.iloc[-1] / df_price.iloc[0]) ** (252 / len(df_price.index)) - 1
    df['Correlation'] = rho[fund_name]
    df['Kendall tau'] = tau[fund_name]
    return df


def make_FXHedge(df_price: pd.DataFrame, df_fx: pd.Series):
    """
    prices need to be in ER
    :param df_price:
    :param df_fx:
    :return:
    """
    dates = df_price.loc[df_fx.index[0]:].index
    price_fx_hedge = pd.DataFrame(index=dates, columns=df_price.columns)
    df_fx = df_fx.reindex(dates).ffill()
    n = len(dates)
    price_fx_hedge.iloc[0] = 1.

    for i in range(1, n):
        price_fx_hedge.iloc[i] = price_fx_hedge.iloc[i-1] * (1 + df_price.iloc[i] * df_fx.iloc[i] /
                                                             (df_price.iloc[i-1] * df_fx.iloc[i-1])
                                                             - df_fx.iloc[i] / df_fx.iloc[i-1])
    return price_fx_hedge
        

def make_ER(price: pd.DataFrame, rate):

    """
    :param price: pd.DataFrame containing the prices of the ticker
    :param rate: pd.Series containing the rate prices
    :return:
    """

    dates = price.loc[rate.index[0]:].index
    price_ER = pd.DataFrame(index=dates, columns=price.columns)
    price_ER.iloc[0] = 1.
    n = len(dates)
    rate = rate.reindex(dates).ffill()
    for i in range(1, n):
        price_ER.iloc[i] = price_ER.iloc[i-1] * (price.iloc[i] / price.iloc[i-1]
                                                 - rate.loc[dates[i-1]] * (dates[i] - dates[i-1]).days / 36000.)
    return price_ER


def make_track(df_price: pd.DataFrame, df_weight: pd.DataFrame, tc=0, lag=1):
    """
    :param df_price: a dataframe containing the prices of the underlyings used in the index, columns must be the names
    and the index are the dates
    :param df_weight: a dataframe containing the weight on the rebalancing dates of the track created
    :param tc: transaction cost, default is 0
    :return: a pandas series containing the track made from the composition in df_weight
    """

    index = df_price.index
    reweight_index = df_weight.index
    n = len(index)
    shares = (df_weight / df_price).iloc[0]
    cash = 1 - (shares * df_price.iloc[0]).sum()  # add cash when weigh_sum <> 1 in ER
    value = np.ones(n)

    for i in range(1, len(index)):
        if index[i-lag] in reweight_index:
            value[i] = (shares * df_price.loc[index[i]]).sum() + cash
            cost = tc * value[i] * np.abs(df_weight.loc[index[i-lag]] - (shares * df_price.loc[index[i]])/value[i]).sum()
            value[i] = value[i] - cost
            shares = df_weight.loc[index[i-lag]] * value[i] / df_price.loc[index[i]]
            cash = value[i] - (shares * df_price.loc[index[i]]).sum()
        else: 
            value[i] = (shares * df_price.loc[index[i]]).sum() + cash

    return pd.DataFrame(index=index, data=value, columns=['Track'])


def ols_regression(df_y: pd.DataFrame, df_x: pd.DataFrame, sample_length: int, frequency: int, vol_target=False,
                   vol_period=20):

    if vol_target and vol_period > sample_length:
        raise Exception("The period for vol_target cannot be longer than sample_length")

    index = df_y.index.copy()
    n, m = df_x.shape

    df_weight = pd.DataFrame(columns=df_x.columns)

    for i in range((n - sample_length)//frequency + 1):
        start = index[i*frequency]
        end = index[i*frequency + sample_length - 1]
        x = df_x.loc[start:end].values
        y = df_y.loc[start:end].values

        weight = sp.linalg.solve(np.dot(x.T, x), np.dot(x.T, y))

        leverage = 1
        if vol_target:
            port_vol = np.std(y[-vol_period:, 0])
            repli_vol = np.std(np.dot(x[-vol_period:, :].nan_to_num(), weight[:, 0]))
            leverage = port_vol / repli_vol
        df_weight.loc[end] = leverage * weight[:, 0].T

    return df_weight.fillna(0)


def lasso_regression(df_y: pd.DataFrame, df_x: pd.DataFrame, sample_length: int, frequency: int, l=0., vol_target=False,
                     vol_period=20):

    if vol_target and vol_period > sample_length:
        raise Exception("The period for vol_target cannot be longer than sample_length")

    index = df_y.index.copy()
    n, m = df_x.shape
    df_weight = pd.DataFrame(columns=df_x.columns)

    for i in range((n - sample_length)//frequency + 1):
        start = index[i*frequency]
        end = index[i*frequency + sample_length - 1]
        stdx = df_x.loc[start:end].std(axis=0, skipna=False).replace({0: np.nan})
        stdy = df_y.loc[start:end].std(axis=0)
        x = (df_x.loc[start:end] / stdx).fillna(0).values
        y = (df_y.loc[start:end] / stdy).values
        
        las = Lasso(alpha=l / (2. * (stdy.iloc[0] ** 2)), fit_intercept=False, normalize=False)
        las.fit(x, y)

        df_weight.loc[end] = las.coef_
        df_weight.loc[end] = df_weight.loc[end] * stdy.iloc[0] / stdx
        weight = df_weight.loc[end].values

        leverage = 1
        if vol_target:
            port_vol = np.std(y[-vol_period:, 0])
            repli_vol = np.std(np.dot(x[-vol_period:, :].nan_to_num(), weight))
            leverage = port_vol / repli_vol
        df_weight.loc[end] = leverage * weight.T

    return df_weight.fillna(0)


def lasso_regression_ic(df_y: pd.DataFrame, df_x: pd.DataFrame, sample_length: int, frequency: int,
                        criterion: str, plot_lambda=True, vol_target=False, vol_period=20):

    if vol_target and vol_period > sample_length:
        raise Exception("The period for vol_target cannot be longer than sample_length")

    index = df_y.index.copy()
    n, m = df_x.shape
    df_weight = pd.DataFrame(columns=df_x.columns)
    df_lambda = pd.DataFrame(columns=['$\lambda$'])

    for i in range((n - sample_length) // frequency + 1):
        start = index[i * frequency]
        vol_start = index[i * frequency + sample_length - vol_period]
        end = index[i * frequency + sample_length - 1]
        stdx = df_x.loc[start:end].std(axis=0, skipna=False).replace({0: np.nan})
        stdy = df_y.loc[start:end].std(axis=0)
        x = (df_x.loc[start:end] / stdx).fillna(0).values
        y = (df_y.loc[start:end] / stdy).values

        las = LassoLarsIC(criterion=criterion, fit_intercept=False, normalize=False)
        las.fit(x, np.ravel(y))

        df_lambda.loc[end] = 2. * las.alpha_ * (stdy.iloc[0] ** 2)
        df_weight.loc[end] = las.coef_
        df_weight.loc[end] = df_weight.loc[end] * stdy.iloc[0] / stdx
        weight = df_weight.loc[end].values

        leverage = 1
        if vol_target:
            port_vol = np.std(df_y.loc[vol_start:end].values)
            repli_vol = np.std(np.dot(df_x.loc[vol_start:end].fillna(0).values, weight))
            leverage = port_vol / repli_vol
        df_weight.loc[end] = leverage * weight

    if plot_lambda:
        sns.set()
        df_lambda['$\lambda$'].plot(title="$\lambda$ parameter selected by the " + criterion.upper())
        plt.show()

    return df_weight.fillna(0), df_lambda


def ridge_regression(df_y: pd.DataFrame, df_x: pd.DataFrame, sample_length: int, frequency: int, l=0., vol_target=False,
                     vol_period=20):

    if vol_target and vol_period > sample_length:
        raise Exception("The period for vol_target cannot be longer than sample_length")

    index = df_y.index.copy()
    n, m = df_x.shape
    I = np.eye(m)
    df_weight = pd.DataFrame(columns=df_x.columns)

    for i in range((n - sample_length)//frequency + 1):
        start = index[i*frequency]
        end = index[i*frequency + sample_length - 1]
        stdx = df_x.loc[start:end].std(axis=0, skipna=False).replace({0 : np.nan})
        stdy = df_y.loc[start:end].std(axis=0)
        x = (df_x.loc[start:end] / stdx).fillna(0).values
        y = (df_y.loc[start:end] / stdy).values

        l1 = l * sample_length / (np.float(m) * (stdy.iloc[0] ** 2))
        weight = sp.linalg.solve(np.dot(x.T, x) + l1 * I, np.dot(x.T, y))

        df_weight.loc[end] = weight[:, 0]
        df_weight.loc[end] = df_weight.loc[end] * stdy.iloc[0] / stdx
        weight = df_weight.loc[end].values

        leverage = 1
        if vol_target:
            port_vol = np.std(y[-vol_period:, 0])
            repli_vol = np.std(np.dot(x[-vol_period:, :].nan_to_num(), weight))
            leverage = port_vol / repli_vol
        df_weight.loc[end] = leverage * weight

    return df_weight.fillna(0)


def kalman_filter(df_y: pd.DataFrame, df_x: pd.DataFrame, frequency: int, sigma_weight: float, sigma_return: float,
                  weight_init=np.array([0]), cov_init=np.array([0]), vol_target=False, vol_period=20,
                  return_log_likelihood=False):

    if vol_target and vol_period < frequency:
        raise Exception("The period for vol_target cannot be shorter than frequency")
    if not vol_target:
        vol_period = frequency

    index = df_y.index.copy()
    n, m = df_x.shape
    I = np.eye(m)
    In = np.eye(frequency)
    cov_weight = (sigma_weight ** 2) * I
    cov_return = (sigma_return ** 2) * np.eye(frequency)
    df_weight = pd.DataFrame(columns=df_x.columns)
    if np.all(weight_init == 0): weight_filter = np.zeros([m, 1])
    else: weight_filter = weight_init
    if np.all(cov_init == 0): cov_filter = np.zeros([m, m])
    else: cov_filter = cov_init
    log_likelihood = 0

    for i in range((n - vol_period)//frequency + 1):
        start = index[vol_period + (i - 1) * frequency]
        vol_start = index[i * frequency]
        end = index[vol_period + i * frequency - 1]
        x = df_x.loc[start:end].fillna(0).values
        y = df_y.loc[start:end].values

        cov_forecast = cov_filter + cov_weight
        temp = np.dot(cov_forecast, x.T)
        gamma = np.dot(x, temp) + cov_return
        K = sp.linalg.solve(gamma.T, temp.T).T
        weight_filter = (weight_filter + np.dot(K, y - np.dot(x, weight_filter)))
        cov_filter = np.dot(I - np.dot(K, x), cov_forecast)

        leverage = 1
        if vol_target:
            port_vol = np.std(df_y.loc[vol_start:end].values)
            repli_vol = np.std(np.dot(df_x.loc[vol_start:end].fillna(0).values, weight_filter[:, 0]))
            leverage = port_vol / repli_vol
        df_weight.loc[end] = leverage * weight_filter[:, 0].T

        log_likelihood += kalman_log_likelihood(gamma, x, y, weight_filter)

    if return_log_likelihood: return log_likelihood
    else: return df_weight.fillna(0)


def kalman_with_selection(df_y: pd.DataFrame, df_x: pd.DataFrame, sample_length: int, frequency: int,
                          nu: float, nb_period: int, criterion: str, vol_target=False, vol_period=20):

    if nb_period > sample_length:
        raise Exception("nb_period cannot be longer than sample_length")
    if vol_target and vol_period > sample_length:
        raise Exception("The period for vol_target cannot be longer than sample_length")

    df_weight_lasso, _ = lasso_regression_ic(df_y, df_x, sample_length, frequency, criterion, plot_lambda=False)
    df_weight = pd.DataFrame(columns=df_x.columns)
    index = df_weight_lasso.index.copy()
    _, m = df_weight.shape

    for date in index:
        selection = df_weight_lasso.loc[date] != 0.0
        selection = list(selection[selection].index)
        if not selection:
            df_weight.loc[date, :] = 0
        else:
            i = df_x.index.get_loc(date)
            df_x_ = df_x[selection].iloc[i-nb_period+1: i+1].fillna(0)
            df_y_ = df_y.loc[df_x_.index]
            weight = np.array(df_weight_lasso.loc[date, selection].values).reshape(m, 1)
            kalman = kalman_filter(df_y=df_y_, df_x=df_x_, frequency=1, sigma_weight=1.,
                                   sigma_return=nu, weight_init=weight)
            weight = kalman.loc[date].values

            leverage = 1
            if vol_target:
                port_vol = np.std(df_y.iloc[i - vol_period + 1:i + 1].values)
                repli_vol = np.std(np.dot(df_x[selection].iloc[i - vol_period + 1:i + 1].fillna(0).values, weight))
                leverage = port_vol / repli_vol
            df_weight.loc[date, selection] = leverage * weight

    return df_weight.fillna(0.0)


def selective_kalman_filter(df_y: pd.DataFrame, df_x: pd.DataFrame, sample_length: int, frequency: int,
                            nu: float, criterion: str, vol_target=False, vol_period=20):

    if vol_target and vol_period > sample_length:
        raise Exception("The period for vol_target cannot be longer than sample_length")

    df_weight_lasso, _ = lasso_regression_ic(df_y, df_x, sample_length, frequency, criterion, plot_lambda=False)
    df_weight = pd.DataFrame(columns=df_x.columns)
    index = df_weight_lasso.index.copy()
    _, m = df_x.shape
    I = np.eye(m)
    cov_weight = I
    cov_return = (nu ** 2) * np.eye(frequency)
    weight_forecast = np.zeros([m, 1])
    cov_filter = np.zeros([m, m])

    for date in index:
        selection = np.diag(df_weight_lasso.loc[date] != 0.0)
        i = df_x.index.get_loc(date)
        x = np.dot(df_x.iloc[i-frequency+1:i+1].fillna(0).values, selection)
        y = df_y.iloc[i-frequency+1:i+1].values

        cov_forecast = cov_filter + cov_weight
        temp = np.dot(cov_forecast, x.T)
        gamma = np.dot(x, temp) + cov_return
        K = sp.linalg.solve(gamma.T, temp.T).T
        weight_forecast = (weight_forecast + np.dot(K, y - np.dot(x, weight_forecast)))
        cov_filter = np.dot(I - np.dot(K, x), cov_forecast)
        weight = np.dot(selection, weight_forecast)

        leverage = 1
        if vol_target:
            port_vol = np.std(df_y.iloc[i-vol_period+1:i+1].values)
            repli_vol = np.std(np.dot(df_x.iloc[i-vol_period+1:i+1].fillna(0).values, weight[:, 0]))
            leverage = port_vol / repli_vol
        df_weight.loc[date] = leverage * weight[:, 0].T

    return df_weight.fillna(0.0)


def ml_kalman_filter(df_y: pd.DataFrame, df_x: pd.DataFrame, frequency: int, tau: float,
                     vol_target=False, vol_period=20, plot_sigma=False):

    if vol_target and vol_period < frequency:
        raise Exception("The period for vol_target cannot be shorter than frequency")
    if not vol_target:
        vol_period = frequency

    index = df_y.index.copy()
    n, m = df_x.shape
    df_weight = pd.DataFrame(columns=df_x.columns)
    df_sigma = pd.DataFrame(columns=[r"$\tilde{\sigma}_{\epsilon}$", r"$\tilde{\sigma}_{\eta}$",
                                     r"$\hat{\sigma}_{\epsilon}$", r"$\hat{\sigma}_{\eta}$"])
    Ip = np.eye(m)
    In = np.eye(frequency)
    weight_filter = np.zeros([m, 1])
    cov_filter = np.zeros([m, m])
    sigma_weight = 1.
    sigma_return = 1.

    for i in range((n - vol_period)//frequency + 1):
        start = index[vol_period + (i - 1) * frequency]
        vol_start = index[i * frequency]
        end = index[vol_period + i * frequency - 1]
        x = df_x.loc[start:end].fillna(0).values
        y = df_y.loc[start:end].values

        theta = max_likelihoog_estimator(sigma_return, sigma_weight, x, y, cov_filter, weight_filter)
        if sigma_weight == 1. and sigma_return == 1.:
            sigma_weight = theta[1]
            sigma_return = theta[0]
        else:
            sigma_weight = tau * theta[1] + (1 - tau) * sigma_weight
            sigma_return = tau * theta[0] + (1 - tau) * sigma_return

        df_sigma.loc[end] = [theta[0], theta[1], sigma_return, sigma_weight]
        cov_weight = (sigma_weight ** 2) * np.eye(m)
        cov_return = (sigma_return ** 2) * np.eye(frequency)

        cov_forecast = cov_filter + cov_weight
        temp = np.dot(cov_forecast, x.T)
        gamma = np.dot(x, temp) + cov_return
        K = sp.linalg.solve(gamma.T, temp.T).T
        weight_filter = (weight_filter + np.dot(K, y - np.dot(x, weight_filter)))
        cov_filter = np.dot(Ip - np.dot(K, x), cov_forecast)

        leverage = 1
        if vol_target:
            port_vol = np.std(df_y.loc[vol_start:end].values)
            repli_vol = np.std(np.dot(df_x.loc[vol_start:end].fillna(0).values, weight_filter[:, 0]))
            leverage = port_vol / repli_vol
        df_weight.loc[end] = leverage * weight_filter[:, 0].T

    df_sigma[r'$\hat{\nu}$'] = df_sigma[r"$\hat{\sigma}_{\epsilon}$"] / df_sigma[r"$\hat{\sigma}_{\eta}$"]
    if plot_sigma:
        fig, (ax1, ax2) = plt.subplots(1, 2)
        sns.set()
        df_sigma[[r"$\tilde{\sigma}_{\epsilon}$", r"$\hat{\sigma}_{\epsilon}$"]].plot(ax=ax1, figsize=(16, 6), logy=True)
        df_sigma[[r"$\tilde{\sigma}_{\eta}$", r"$\hat{\sigma}_{\eta}$"]].plot(ax=ax2, figsize=(16, 6), logy=True)
        plt.show()
        df_sigma[r'$\hat{\nu}$'].plot(figsize=(16, 8), logy=True)
        plt.show()

    return df_weight.fillna(0), df_sigma


def ml_kalman_filter2(df_y: pd.DataFrame, df_x: pd.DataFrame, frequency: int, mle_period: int,
                     vol_target=False, vol_period=20, plot_sigma=False):

    if vol_target and vol_period < frequency:
        raise Exception("The period for vol_target cannot be shorter than frequency")
    if not vol_target:
        vol_period = frequency

    index = df_y.index.copy()
    n, m = df_x.shape
    df_weight = pd.DataFrame(columns=df_x.columns)
    df_sigma = pd.DataFrame(columns=[r"$\hat{\sigma}_{\epsilon}$", r"$\hat{\sigma}_{\eta}$"])
    weight_filter = np.zeros([m, 1])
    cov_filter = np.zeros([m, m])
    Ip = np.eye(m)
    In = np.eye(frequency)
    sigma_weight = 1.
    sigma_return = 1.

    weight_list = [weight_filter]
    cov_list = [cov_filter]

    for i in range((n - vol_period)//frequency + 1):
        start = index[vol_period + (i - 1) * frequency]
        vol_start = index[i * frequency]
        end = index[vol_period + i * frequency - 1]
        x = df_x.loc[start:end].fillna(0).values
        y = df_y.loc[start:end].values

        if len(weight_list) >= mle_period and len(cov_list) >= mle_period:
            weight_mle_start = weight_list.pop(0)
            cov_mle_start = cov_list.pop(0)
        else:
            weight_mle_start = weight_list[0]
            cov_mle_start = cov_list[0]
        x_mle = df_x.iloc[vol_period+max([i-mle_period,-1])*frequency:vol_period+i*frequency]
        y_mle = df_y.iloc[vol_period+max([i-mle_period,-1])*frequency:vol_period+i*frequency]
        theta = max_likelihoog_estimator2(sigma_return, sigma_weight, x_mle, y_mle, frequency, weight_mle_start,
                                          cov_mle_start)
        sigma_weight = theta[1]
        sigma_return = theta[0]
        df_sigma.loc[end] = [sigma_return, sigma_weight]
        cov_weight = (sigma_weight ** 2) * np.eye(m)
        cov_return = (sigma_return ** 2) * np.eye(frequency)

        cov_forecast = cov_filter + cov_weight
        temp = np.dot(cov_forecast, x.T)
        gamma = np.dot(x, temp) + cov_return
        K = sp.linalg.solve(gamma.T, temp.T).T
        weight_filter = (weight_filter + np.dot(K, y - np.dot(x, weight_filter)))
        cov_filter = np.dot(Ip - np.dot(K, x), cov_forecast)

        leverage = 1
        if vol_target:
            port_vol = np.std(df_y.loc[vol_start:end].values)
            repli_vol = np.std(np.dot(df_x.loc[vol_start:end].fillna(0).values, weight_filter[:, 0]))
            leverage = port_vol / repli_vol

        df_weight.loc[end] = leverage * weight_filter[:, 0].T

    df_sigma[r'$\hat{\nu}$'] = df_sigma[r"$\hat{\sigma}_{\epsilon}$"] / df_sigma[r"$\hat{\sigma}_{\eta}$"]
    if plot_sigma:
        fig, (ax1, ax2) = plt.subplots(1, 2)
        sns.set()
        df_sigma[[r"$\hat{\sigma}_{\eta}$", r"$\hat{\sigma}_{\epsilon}$"]].plot(ax=ax1, figsize=(16, 6),
                                                                                secondary_y=[r"$\hat{\sigma}_{\epsilon}$"])
        df_sigma[[r'$\hat{\nu}$']].plot(ax=ax2, figsize=(16, 6))
        plt.show()

    return df_weight.fillna(0), df_sigma


def max_likelihoog_estimator(sigma_return, sigma_weight, x, y, cov_filter, weight_filter):
    n, p = x.shape
    In = np.eye(n)
    Ip = np.eye(p)

    def fun(theta):
        theta = theta.reshape(2, )
        sigma_r = 10 ** theta[0]
        sigma_w = 10 ** theta[1]
        gamma = np.linalg.multi_dot([x, cov_filter + (sigma_w ** 2) * Ip, x.T]) + (sigma_r ** 2) * In
        _, logdet = np.linalg.slogdet(gamma)
        pred_return = np.dot(x, weight_filter)
        error = y - pred_return
        try:
            temp = sp.linalg.solve(gamma, error)
            return (logdet + np.dot(error.T, temp))[0]
        except:
            return np.inf

    res = minimize(fun, np.log10(np.array([sigma_return, sigma_weight])), method='Nelder-Mead')
    return 10 ** res.x


def kalman_log_likelihood(gamma, x, y, weight_filter):
    _, logdet = np.linalg.slogdet(gamma)
    pred_return = np.dot(x, weight_filter)
    error = y - pred_return
    try:
        temp = sp.linalg.solve(gamma, error)
        return (logdet + np.dot(error.T, temp))[0]
    except:
        return np.inf


def max_likelihoog_estimator2(sigma_return, sigma_weight, df_x, df_y, frequency, weight_mle_start, cov_mle_start):

    def fun(theta):
        try:
            likelihood = kalman_filter(df_y, df_x, frequency, 10 ** theta[1], 10 ** theta[0], cov_init=cov_mle_start,
                                       weight_init=weight_mle_start, return_log_likelihood=True)
            return likelihood
        except: return np.inf

    options = {'maxiter': 100, 'gtol': frequency * 1e-1, 'eps': 1e-3, 'ftol': frequency * 1e-3, 'maxfun':100}
    bounds = ((-10, 10), (-10, 10))
    res = minimize(fun, np.log10(np.array([sigma_return, sigma_weight])), method='L-BFGS-B', bounds=bounds,
                   options=options)
    return 10 ** res.x