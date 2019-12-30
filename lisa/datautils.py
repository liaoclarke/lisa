# SPDX-License-Identifier: Apache-2.0
#
# Copyright (C) 2019, Arm Limited and contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import functools
import operator
import math

import numpy as np
import pandas as pd
import scipy.integrate
import scipy.signal

from lisa.utils import TASK_COMM_MAX_LEN


def series_refit_index(series, start=None, end=None, method='inclusive'):
    """
    Slice a series using :func:`series_window` and ensure we have a value at
    exactly the specified boundaries.

    :param df: Series to act on
    :type df: pandas.Series

    :param start: First index value to find in the returned series.
    :type start: object

    :param end: Last index value to find in the returned series.
    :type end: object

    :param method: Windowing method used to select the first and last values of
        the series using :func:`series_window`. Defaults to ``pre``, which is
        suitable for signals where all the value changes have a corresponding
        row without any fixed sample-rate constraints. If they have been
        downsampled, ``nearest`` might be a better choice.).
    """

    return _data_refit_index(series, start, end, method=method)


def df_refit_index(df, start=None, end=None, method='inclusive'):
    """
    Same as :func:`series_refit_index` but acting on :class:`pandas.DataFrame`
    """
    return _data_refit_index(df, start, end, method=method)


def df_split_signals(df, signal_cols, align_start=False):
    """
    Yield subset of ``df`` that only contain one signal, along with the signal
    identification values.

    :param df: The dataframe to split.
    :type df: pandas.DataFrame

    :param signal_cols: Columns that uniquely identify a signal.
    :type signal_cols: list(str)

    :param align_start: If ``True``, :func:`df_refit_index` will be applied on
        the yielded dataframes so that they all start at the same index.
    :type refit_index: bool
    """
    if not signal_cols:
        yield ({}, df)
    else:
        for group, signal in df.groupby(signal_cols):
            # When only one column is looked at, the group is the value instead of
            # a tuple of values
            if len(signal_cols) < 2:
                cols_val = {signal_cols[0]: group}
            else:
                cols_val = dict(zip(signal_cols, group))

            if align_start:
                signal = df_refit_index(signal, start=df.index[0], method='inclusive')
            yield (cols_val, signal)


def _data_refit_index(data, start, end, method):
    if data.empty:
        return data

    data = _data_window(data, (start, end), method=method, clip_window=True)
    index = data.index.to_series()

    if end is not None:
        index.iloc[-1] = end

    # If the dataframe has one row, we want the "start" timestamp to be used
    # rather than "end", so set iloc[0] last
    if start is not None:
        index.iloc[0] = start

    # Shallow copy is enough, we only want to replace the index and not the
    # actual data
    data = data.copy(deep=False)
    data.index = index
    return data


def df_squash(df, start, end, column='delta'):
    """
    Slice a dataframe of deltas in [start:end] and ensure we have
    an event at exactly those boundaries.

    The input dataframe is expected to have a "column" which reports
    the time delta between consecutive rows, as for example dataframes
    generated by add_events_deltas().

    The returned dataframe is granted to have an initial and final
    event at the specified "start" ("end") index values, which values
    are the same of the last event before (first event after) the
    specified "start" ("end") time.

    Examples:

    Slice a dataframe to [start:end], and work on the time data so that it
    makes sense within the interval.

    Examples to make it clearer::

        df is:
        Time len state
        15    1   1
        16    1   0
        17    1   1
        18    1   0
        -------------

        df_squash(df, 16.5, 17.5) =>

        Time len state
        16.5  .5   0
        17    .5   1

        df_squash(df, 16.2, 16.8) =>

        Time len state
        16.2  .6   0

    :returns: a new df that fits the above description
    """
    if df.empty:
        return df

    end = min(end, df.index[-1] + df[column].values[-1])
    res_df = pd.DataFrame(data=[], columns=df.columns)

    if start > end:
        return res_df

    # There's a few things to keep in mind here, and it gets confusing
    # even for the people who wrote the code. Let's write it down.
    #
    # It's assumed that the data is continuous, i.e. for any row 'r' within
    # the trace interval, we will find a new row at (r.index + r.len)
    # For us this means we'll never end up with an empty dataframe
    # (if we started with a non empty one)
    #
    # What's we're manipulating looks like this:
    # (| = events; [ & ] = start,end slice)
    #
    # |   [   |   ]   |
    # e0  s0  e1  s1  e2
    #
    # We need to push e0 within the interval, and then tweak its duration
    # (len column). The mathemagical incantation for that is:
    # e0.len = min(e1.index - s0, s1 - s0)
    #
    # This takes care of the case where s1 isn't in the interval
    # If s1 is in the interval, we just need to cap its len to
    # s1 - e1.index

    prev_df = df[:start]
    middle_df = df[start:end]

    # Tweak the closest previous event to include it in the slice
    if not prev_df.empty and not (start in middle_df.index):
        res_df = res_df.append(prev_df.tail(1))
        res_df.index = [start]
        e1 = end

        if not middle_df.empty:
            e1 = middle_df.index[0]

        res_df[column] = min(e1 - start, end - start)

    if not middle_df.empty:
        res_df = res_df.append(middle_df)
        if end in res_df.index:
            # e_last and s1 collide, ditch e_last
            res_df = res_df.drop([end])
        else:
            # Fix the delta for the last row
            delta = min(end - res_df.index[-1], res_df[column].values[-1])
            res_df.at[res_df.index[-1], column] = delta

    return res_df


def df_filter(df, filter_columns):
    """
    Filter the content of a dataframe.

    :param df: DataFrame to filter
    :type df: pandas.DataFrame

    :param filter_columns: Dict of `{"column": value)` that rows has to match
        to be selected.
    :type filter_columns: dict(str, object)
    """
    key = functools.reduce(
        operator.and_,
        (
            df[col] == val
            for col, val in filter_columns.items()
        )
    )

    return df[key]


def df_merge(df_list, drop_columns=None, drop_inplace=False, filter_columns=None):
    """
    Merge a list of :class:`pandas.DataFrame`, keeping the index sorted.

    :param drop_columns: List of columns to drop prior to merging. This avoids
        ending up with extra renamed columns if some dataframes have column
        names in common.
    :type drop_columns: list(str)

    :param drop_inplace: Drop columns in the original dataframes instead of
        creating copies.
    :type drop_inplace: bool

    :param filter_columns: Dict of `{"column": value)` used to filter each
        dataframe prior to dropping columns. The columns are then dropped as
        they have a constant value.
    :type filter_columns: dict(str, object)
    """

    drop_columns = drop_columns if drop_columns else []

    if filter_columns:
        df_list = [
            df_filter(df, filter_columns)
            for df in df_list
        ]

        # remove the column to avoid duplicated useless columns
        drop_columns.extend(filter_columns.keys())
        # Since we just created dataframe slices, drop_inplace would give a
        # warning from pandas
        drop_inplace = False

    if drop_columns:
        def drop(df):
            filtered_df = df.drop(columns=drop_columns, inplace=drop_inplace)
            # when inplace=True, df.drop() returns None
            return df if drop_inplace else filtered_df

        df_list = [
            drop(df)
            for df in df_list
        ]

    def merge(df1, df2):
        return pd.merge(df1, df2, left_index=True, right_index=True, how='outer')

    return functools.reduce(merge, df_list)


def _resolve_x(y, x):
    """
    Resolve the `x` series to use for derivative and integral operations
    """

    if x is None:
        x = pd.Series(y.index)
        x.index = y.index
    return x


def series_derivate(y, x=None, order=1):
    """
    Compute a derivative of a :class:`pandas.Series` with respect to another
    series.

    :return: A series of `dy/dx`, where `x` is either the index of `y` or
        another series.

    :param y: Series with the data to derivate.
    :type y: pandas.DataFrame

    :param x: Series with the `x` data. If ``None``, the index of `y` will be
        used. Note that `y` and `x` are expected to have the same index.
    :type y: pandas.DataFrame or None

    :param order: Order of the derivative (1 is speed, 2 is acceleration etc).
    :type order: int
    """
    x = _resolve_x(y, x)

    for i in range(order):
        y = y.diff() / x.diff()

    return y


def series_integrate(y, x=None, sign=None, method='rect', rect_step='post'):
    """
    Compute the integral of `y` with respect to `x`.

    :return: A scalar :math:`\\int_{x=A}^{x=B} y \\, dx`, where `x` is either the
        index of `y` or another series.

    :param y: Series with the data to integrate.
    :type y: pandas.DataFrame

    :param x: Series with the `x` data. If ``None``, the index of `y` will be
        used. Note that `y` and `x` are expected to have the same index.
    :type y: pandas.DataFrame or None

    :param sign: Clip the data for the area in positive
        or negative regions. Can be any of:

        - ``+``: ignore negative data
        - ``-``: ignore positive data
        - ``None``: use all data

    :type sign: str or None

    :param method: The method for area calculation. This can
        be any of the integration methods supported in :mod:`numpy`
        or `rect`
    :type param: str

    :param rect_step: The step behaviour for `rect` method
    :type rect_step: str

    *Rectangular Method*

        - Step: Post

            Consider the following time series data::

                2            *----*----*----+
                             |              |
                1            |              *----*----+
                             |
                0  *----*----+
                   0    1    2    3    4    5    6    7

                import pandas as pd
                a = [0, 0, 2, 2, 2, 1, 1]
                s = pd.Series(a)

            The area under the curve is:

            .. math::

                \\sum_{k=0}^{N-1} (x_{k+1} - {x_k}) \\times f(x_k) \\\\
                (2 \\times 3) + (1 \\times 2) = 8

        - Step: Pre

            ::

                2       +----*----*----*
                        |              |
                1       |              +----*----*----+
                        |
                0  *----*
                   0    1    2    3    4    5    6    7

                import pandas as pd
                a = [0, 0, 2, 2, 2, 1, 1]
                s = pd.Series(a)

            The area under the curve is:

            .. math::

                \\sum_{k=1}^{N} (x_k - x_{k-1}) \\times f(x_k) \\\\
                (2 \\times 3) + (1 \\times 3) = 9
    """

    x = _resolve_x(y, x)

    if sign == "+":
        y = y.clip(lower=0)
    elif sign == "-":
        y = y.clip(upper=0)
    elif sign is None:
        pass
    else:
        raise ValueError('Unsupported "sign": {}'.format(sign))

    if method == "rect":
        dx = x.diff()

        if rect_step == "post":
            dx = dx.shift(-1)

        return (y * dx).sum()

    # Make a DataFrame to make sure all rows stay aligned when we drop NaN,
    # which is needed by all the below methods
    df = pd.DataFrame({'x': x, 'y': y}).dropna()
    x = df['x']
    y = df['y']

    if method == 'trapz':
        return np.trapz(y, x)

    elif method == 'simps':
        return scipy.integrate.simps(y, x)

    else:
        raise ValueError('Unsupported integration method: {}'.format(method))


def series_mean(y, x=None, **kwargs):
    r"""
    Compute the average of `y` by integrating with respect to `x` and dividing
    by the range of `x`.

    :return: A scalar :math:`\int_{x=A}^{x=B} \frac{y}{| B - A |} \, dx`,
        where `x` is either the index of `y` or another series.

    :param y: Series with the data to integrate.
    :type y: pandas.DataFrame

    :param x: Series with the `x` data. If ``None``, the index of `y` will be
        used. Note that `y` and `x` are expected to have the same index.
    :type y: pandas.DataFrame or None

    :Variable keyword arguments: Forwarded to :func:`series_integrate`.
    """
    x = _resolve_x(y, x)
    integral = series_integrate(y, x, **kwargs)

    return integral / (x.max() - x.min())


def series_window(series, window, method='inclusive', clip_window=True):
    """
    Select a portion of a :class:`pandas.Series`

    :param series: series to slice
    :type series: :class:`pandas.Series`

    :param window: two-tuple of index values for the start and end of the
        region to select.
    :type window: tuple(object)

    :param clip_window: Clip the requested window to the bounds of the index,
        otherwise raise exceptions if the window is too large.
    :type clip_window: bool

    :param method: Choose how edges are handled:

        * `inclusive`: corresponds to default pandas float slicing behaviour.
        * `exclusive`: When no exact match is found, only index values within
            the range are selected
        * `nearest`: When no exact match is found, take the nearest index value.
        * `pre`: When no exact match is found, take the previous index value.
        * `post`: When no exact match is found, take the next index value.

    .. note:: The index of `series` must be monotonic and without duplicates.
    """
    return _data_window(series, window, method, clip_window)


def _data_window(data, window, method='inclusive', clip_window=False):
    """
    ``data`` can either be a :class:`pandas.DataFrame` or :class:`pandas.Series`
    """

    index = data.index
    if clip_window:
        start, end = window
        first = index[0]
        last = index[-1]

        # Fill placeholders
        if start is None:
            start = first
        if end is None:
            end = last

        # Window is on the left
        if start <= first and end <= first:
            start = first
            end = first
        # Window is on the rigth
        elif start >= last and end >= last:
            start = last
            end = last
        # Overlapping window
        else:
            if start <= first:
                start = first

            if end >= last:
                end = last

        window = (start, end)

    if method == 'inclusive':
        # Default slicing behaviour of pandas' Float64Index is to be inclusive,
        # so we can use that knowledge to enable a fast path for common needs.
        if isinstance(data.index, pd.Float64Index):
            return data[slice(*window)]

        method = ('ffill', 'bfill')

    elif method == 'exclusive':
        method = ('bfill', 'ffill')

    elif method == 'nearest':
        method = ('nearest', 'nearest')

    elif method == 'pre':
        method = ('ffill', 'ffill')

    elif method == 'post':
        method = ('bfill', 'bfill')

    else:
        raise ValueError('Slicing method not supported: {}'.format(method))

    window = [
        index.get_loc(x, method=method) if x is not None else None
        for x, method in zip(window, method)
    ]
    window = window[0], (window[1] + 1)

    return data.iloc[slice(*window)]


def df_window(df, window, method='inclusive', clip_window=True):
    """
    Same as :func:`series_window` but acting on a :class:`pandas.DataFrame`
    """
    return _data_window(df, window, method, clip_window)


def df_window_signals(df, window, signal_cols, compress_init=False):
    """
    Similar to :func:`df_window` with ``method='pre'`` but guarantees that each
    signal will have a values at the beginning of the window.

    :param window: two-tuple of index values for the start and end of the
        region to select.
    :type window: tuple(object)

    :param signal_cols: Columns that uniquely identify a signal.
    :type signal_cols: list(str)

    :param compress_init: When ``False``, the timestamps of the init value of
        signals (right before the window) are preserved. If ``True``, they are
        changed into values as close as possible to the beginning of the window.
    :type compress_init: bool

    .. seealso:: :func:`df_split_signals`
    """

    def signal_in_window(signal_df, window):
        start = window[0]
        index = signal_df.index
        signal_start, signal_end = index[0], index[-1]
        # Signals are encoded as transitions, so as soon as we a transition
        # inside the window, we know that the signal is relevant
        return signal_start <= start <= signal_end

    # Get the value of each signal at the beginning of the window
    signal_df_list = [
        df_window(signal_df, window, method='pre')
        for signal, signal_df in df_split_signals(df, signal_cols, align_start=False)
        # Only consider the signal that are in the window. Signals that started
        # after the window are irrelevant.
        if signal_in_window(signal_df, window)
    ]

    windowed_df = df_window(df, window, method='pre')

    if compress_init:
        def make_init_df_index(init_df):
            # Yield a sequence of numbers incrementing by the smallest amount
            # possible
            def smallest_increment(start, length):
                curr = start
                for _ in range(length):
                    curr = np.nextafter(curr, -math.inf)
                    yield curr

            index = list(smallest_increment(windowed_df.index[0], len(init_df)))
            index = pd.Float64Index(reversed(index))
            return index
    else:
        def make_init_df_index(init_df):
            return init_df.index

    # Get the last row before the beginning the window for each signal, in
    # timestamp order
    init_df = pd.concat(
        # First row of the dataframe
        signal_df.iloc[0:1]
        for signal_df in sorted(signal_df_list, key=lambda df: df.index[0])
    )

    init_df.index = make_init_df_index(init_df)
    return pd.concat([init_df, windowed_df])


def series_align_signal(ref, to_align, max_shift=None):
    """
    Align a signal to an expected reference signal using their
    cross-correlation.

    :returns: `(ref, to_align)` tuple, with `to_align` shifted by an amount
        computed to align as well as possible with `ref`. Both `ref` and
        `to_align` are resampled to have a fixed sample rate.

    :param ref: reference signal.
    :type ref: pandas.Series

    :param to_align: signal to align
    :type to_align: pandas.Series

    :param max_shift: Maximum shift allowed to align signals, in index units.
    :type max_shift: object or None
    """
    if ref.isnull().any() or to_align.isnull().any():
        raise ValueError('NaN needs to be dropped prior to alignment')

    # Select the overlapping part of the signals
    start = max(ref.index.min(), to_align.index.min())
    end = min(ref.index.max(), to_align.index.max())

    # Resample so that we operate on a fixed sampled rate signal, which is
    # necessary in order to be able to do a meaningful interpretation of
    # correlation argmax
    def get_period(series): return pd.Series(series.index).diff().min()
    period = min(get_period(ref), get_period(to_align))
    num = math.ceil((end - start) / period)
    new_index = pd.Float64Index(np.linspace(start, end, num))

    to_align = to_align.reindex(new_index, method='ffill')
    ref = ref.reindex(new_index, method='ffill')

    # Compute the correlation between the two signals
    correlation = scipy.signal.signaltools.correlate(to_align, ref)
    # The most likely shift is the index at which the correlation is
    # maximum. correlation.argmax() can vary from 0 to 2*len(to_align), so we
    # re-center it.
    shift = correlation.argmax() - len(to_align)

    # Cap the shift value
    if max_shift is not None:
        assert max_shift >= 0

        # Turn max_shift into a number of samples in the resampled signal
        max_shift = int(max_shift / period)

        # Adjust the sign of max_shift to match shift
        max_shift *= -1 if shift < 0 else 1

        if abs(shift) > abs(max_shift):
            shift = max_shift

    # Compensate the shift
    return ref, to_align.shift(-shift)


def df_filter_task_ids(df, task_ids, pid_col='pid', comm_col='comm', invert=False, comm_max_len=TASK_COMM_MAX_LEN):
    """
    Filter a dataframe using a list of :class:`lisa.trace.TaskID`

    :param task_ids: List of task IDs to filter
    :type task_ids: list(lisa.trace.TaskID)

    :param df: Dataframe to act on.
    :type df: pandas.DataFrame

    :param pid_col: Column name in the dataframe with PIDs.
    :type pid_col: str or None

    :param comm_col: Column name in the dataframe with comm.
    :type comm_col: str or None

    :param comm_max_len: Maximum expected length of the strings in
        ``comm_col``. The ``task_ids`` `comm` field will be truncated at that
        length before being matched.

    :param invert: Invert selection
    :type invert: bool
    """

    def make_filter(task_id):
        if pid_col and task_id.pid is not None:
            pid = (df[pid_col] == task_id.pid)
        else:
            pid = True
        if comm_col and task_id.comm is not None:
            comm = (df[comm_col] == task_id.comm[:comm_max_len])
        else:
            comm = True

        return pid & comm

    tasks_filters = map(make_filter, task_ids)

    # Combine all the task filters with OR
    tasks_filter = functools.reduce(operator.or_, tasks_filters, False)

    if invert:
        tasks_filter = ~tasks_filter

    return df[tasks_filter]


def series_local_extremum(series, kind):
    """
    Returns a series of local extremum.

    :param series: Series to look at.
    :type series: pandas.Series

    :param kind: Kind of extremum: ``min`` or ``max``.
    :type kind: str
    """
    if kind == 'min':
        comparator = np.less_equal
    elif kind == 'max':
        comparator = np.greater_equal
    else:
        raise ValueError('Unsupported kind: {}'.format(kind))

    ilocs = scipy.signal.argrelextrema(series.values, comparator=comparator)
    return series.iloc[ilocs]


def series_tunnel_mean(series):
    """
    Compute the average between the mean of local maximums and local minimums
    of the series.

    Assuming that the values are ranging inside a tunnel, this will give the
    average center of that tunnel.
    """
    maxs = series_local_extremum(series, kind='max')
    mins = series_local_extremum(series, kind='min')

    maxs_mean = series_mean(maxs)
    mins_mean = series_mean(mins)

    return (maxs_mean - mins_mean) / 2 + mins_mean


def series_rolling_apply(series, func, window, window_float_index=True, center=False):
    """
    Apply a function on a rolling window of a series.

    :returns: The series of results of the function.

    :param series: Series to act on.
    :type series: pandas.Series

    :param func: Function to apply on each window. It must take a
        :class:`pandas.Series` as only parameter and return one value.
    :type func: collections.abc.Callable

    :param window: Rolling window width in seconds.
    :type window: float

    :param center: Label values generated by ``func`` with the center of the
        window, rather than the highest index in it.
    :type center: bool

    :param window_float_index: If ``True``, the series passed to ``func`` will
        be of type :class:`pandas.Float64Index`, in nanoseconds. Disabling is
        recommended if the index is not used by ``func`` since it will remove
        the need for a conversion.
    :type window_float_index: bool
    """
    orig_index = series.index

    # Wrap the func to turn the index into nanosecond Float64Index
    if window_float_index:
        def func(s, func=func):
            s.index = s.index.astype('int64') * 1e-9
            return func(s)

    # Use a timedelta index so that rolling gives time-based results
    index = pd.to_timedelta(orig_index, unit='s')
    series = pd.Series(series.values, index=index)

    window_ns = int(window * 1e9)
    rolling_window = '{}ns'.format(window_ns)
    values = series.rolling(rolling_window).apply(func, raw=False).values

    if center:
        new_index = orig_index - (window / 2)
    else:
        new_index = orig_index

    return pd.Series(values, index=new_index)


def _data_deduplicate(data, keep, consecutives, cols, all_col):
    if keep == 'first':
        shift = 1
    elif keep == 'last':
        shift = -1
    else:
        raise ValueError('Unknown keep value: {}'.format(keep))

    if consecutives:
        dedup_data = data[cols] if cols else data
        cond = dedup_data != dedup_data.shift(shift)
        if isinstance(data, pd.DataFrame):
            # The test is somewhat inverted since the cond must be True when
            # the data is selected, but all_col is defined in terms of rejected
            # data:
            # not ((not x) and (not y))
            # not (not (x or y))
            # x or y
            if all_col:
                cond = cond.any(axis=1)
            # not ((not x) or (not y))
            # not (not (x and y))
            # x and y
            else:
                cond = cond.all(axis=1)

        return data[cond]
    else:
        if not all_col:
            raise ValueError("all_col=False is not supported with consecutives=False")

        kwargs = dict(subset=cols) if cols else {}
        return data.drop_duplicates(keep=keep, **kwargs)


def series_deduplicate(series, keep, consecutives):
    """
    Remove duplicate values in a :class:`pandas.Series`.

    :param keep: Keep the first occurrences if ``first``, or the last if
        ``last``.
    :type keep: str

    :param consecutives: If ``True``, will only remove consecutive duplicates,
        for example::

            s = pd.Series([1,2,2,3,4,2], index=[1,2,20,30,40,50])
            s2 = series_deduplicate(s, keep='first', consecutives=True)
            assert (s2 == [1,2,3,4,2]).all()

            s3 = series_deduplicate(s, keep='first', consecutives=False)
            assert (s3 == [1,2,3,4]).all()

    :type consecutives: bool
    """
    return _data_deduplicate(series, keep=keep, consecutives=consecutives, cols=None, all_col=None)


def df_deduplicate(df, keep, consecutives, cols=None, all_col=True):
    """
    Same as :func:`series_deduplicate` but for :class:`pandas.DataFrame`.

    :param cols: Only consider these columns when looking for duplicates.
        By default, all columns are considered
    :type cols: list(str) or None

    :param all_col: If ``True``, remove a row when all the columns have duplicated value.
        Otherwise, remove the row if any column is duplicated.
    :type all_col: bool
    """
    return _data_deduplicate(df, keep=keep, consecutives=consecutives, cols=cols, all_col=all_col)

# vim :set tabstop=4 shiftwidth=4 textwidth=80 expandtab
