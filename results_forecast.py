''' attempt to forecast best option from probable effects '''
# Standard
import math
import pathlib
import random
import sys
import re

# Typing
from typing import Dict, List, Optional, Set, Tuple

# External
import requests
import lxml.html
import pandas

INFINITE = float('inf')
EXPONENT_BASE = 11.3 # float greater than one
REQUEST_HEADERS = {'content-type': 'text/html'}
ptrn_grps = dict(num=r'([-+]?\d+(?:\.\d+)?)', census=r'([^\.\d]+)')
effect_pattern = re.compile('^{num} to {num} {census} \(mean {num}\)$'.format(**ptrn_grps))
simple_pattern = re.compile('^{num} {census}$'.format(**ptrn_grps))

def main(nation: str=None, issue: str=None):
    while nation is None or not nation.isalnum():
        nation = input('nation: ')
        scales_file = pathlib.Path(nation + '_category_scale.csv')
        if scales_file.is_file():
            break
        print(f'Category csv for nationstate: "{nation}" not found.')
        nation = None
    while issue is None or not issue.isdecimal():
        issue = input('issue: ')
        if issue != '?':
            continue
        break
    option = 'n ' + issue

    doc: Optional[lxml.html.HtmlElement] = None
    excluded: Set[str] = set()
    census_filter = True
    cumsum = False
    while True:
        match = re.search(r'n (?P<issue>\d{1,4}|\?)', option)
        if match:
            issue_num, doc = get_issue(match['issue'])
        elif option == '0':
            excluded.clear()
        elif option == 'f':
            census_filter = not census_filter
        elif option == 'c':
            cumsum = not cumsum
        elif option == 'e':
            break
        elif option in excluded:
            excluded.remove(option)
        else:
            excluded.add(option)
        assert doc is not None
        scales_df, options = build_dataframes(nation, doc, excluded)
        summarize_results(scales_df, options, census_filter, cumsum)
        print('https://nsindex.net/wiki/NationStates_Issue_No._%d\n' % issue_num)
        print('https://www.nationstates.net/page=show_dilemma/dilemma=%d\n' % issue_num)
        option = None
        while not option:
            option = input(
                '"f" > toggle zero bias\n'
                '"c" > toggle cumulative summation\n'
                '"n (\d{1,4}|\?)" > reset with new issue number\n'
                '"1-9" > drop/restore option\n'
                '"0" > reset options\n'
                '"e" > exit\n'
                '>> ')

def get_issue(issue: str):
    url = 'http://www.mwq.dds.nl/ns/results/'
    if issue == '?':
        page = requests.get(url, headers=REQUEST_HEADERS)
        doc: lxml.html.HtmlElement = lxml.html.fromstring(page.content)
        text_content: str = doc.text_content()
        regex = re.findall('#(\d+) [^\?]', text_content)
        issue = random.choice(regex)

    page = requests.get(f'{url}{issue}.html', headers=REQUEST_HEADERS)
    doc: lxml.html.HtmlElement = lxml.html.fromstring(page.content)
    return int(issue), doc

def summarize_results(scales_df: pandas.DataFrame, options: pandas.DataFrame, census_filter: bool, cumsum: bool):
    scales_df.dropna(thresh=2, inplace=True)
    scales_df.sort_index(inplace=True)
    if not options.empty:
        scales_df['magnitude'] = pandas.Series(abs(scales_df.bias), scales_df.index)
        scales_df['direction'] = pandas.Series(scales_df.bias > 0, scales_df.index)
        sort_cols = ['magnitude', 'direction']
        scales_df.sort_values(by=sort_cols, ascending=not cumsum, inplace=True)
        scales_df = scales_df.drop(sort_cols, 1).fillna(0)
    if cumsum:
        bias_col, *option_cols = scales_df.columns
        for option in option_cols:
            scales_df[option] = (scales_df[bias_col] * scales_df[option]).cumsum()
        scales_df['0.'] = scales_df[bias_col] * 0
        option_cols.insert(0, '0.')
        scales_T = scales_df.T
        for census in scales_T.columns:
            weight_series = scales_T.pop(census).copy()
            bias = weight_series['bias']
            weight_series['bias'] = -INFINITE
            new_row = probability_list(weight_series).astype(float)
            new_row['bias'] = bias
            scales_T[census] = new_row
        scales_TT = scales_T.T
        scales_df = pandas.merge(scales_TT[bias_col].astype(float), scales_TT[option_cols].astype(int), left_index=True, right_index=True)
    with pandas.option_context('display.max_colwidth', -1, 'display.float_format', '{:.4f}'.format, 'display.precision', 4):
        if census_filter:
            scales_df = scales_df[scales_df.bias != 0]
        print(scales_df.to_string() + '\n')
        print(options.fillna('').to_string(index=False) + '\n')
    return

def build_dataframes(nation: str, doc: lxml.html.HtmlElement, excluded: Set[str]):
    scales_file = pathlib.Path(nation + '_category_scale.csv')
    assert scales_file.is_file(), scales_file.name
    scales_df = pandas.read_csv(scales_file, names=('census', 'bias'), index_col='census')
    category_scales: Dict[str, float] = scales_df.to_dict('dict')['bias']

    policies_file = pathlib.Path(nation + '_policy_exclusions.csv')
    if policies_file.is_file():
        df = pandas.read_csv(policies_file, names=('policy', 'change')).dropna().to_dict('records')
        excluded_policy_reforms = tuple('{change} policy: {policy}'.format(**row) for row in df)
    else:
        excluded_policy_reforms = ()

    extras: List[Dict[str, str]]
    title, *extras = doc.xpath('//title')
    assert not extras
    assert isinstance(title, lxml.html.HtmlElement)
    print(title.text)
    cols: List[str] = (
        'option,datums,net_result,percent,headline,resigns from,leads to,'
        'adds,removes,sometimes adds,sometimes removes,may add or remove').split(',')
    option_summary = dict(option='0.', datums=None, net_result=0, headline='Dismiss issue.')
    option_list = [option_summary]
    elements: Tuple[lxml.html.HtmlElement, lxml.html.HtmlElement, lxml.html.HtmlElement]
    for elements in doc.xpath('//tr')[1:]:
        result, effects, observations = elements
        result_text: str = result.text_content()
        option_text, headline = result_text.strip().split(' ', 1)
        deltas, unparsed_strs, datums = weigh_option(effects, observations)
        if any(option_text.startswith(option_str) for option_str in excluded):
            weight = -INFINITE
            extras = {}
        else:
            scales_df[option_text] = [deltas.get(category) for category in scales_df.index]
            weight: float = sum(category_scales[category] * deltas[category] for category in deltas)
            extras = split_unparsed_strings(unparsed_strs)
            if any(reform in unparsed_strs for reform in excluded_policy_reforms):
                option_text += ' policy reform'
        if extras:
            first_row = extras.pop(0)
            cols.extend(key for key in first_row if key not in cols)
        else:
            first_row: Dict[str, str] = {}
        headline, *unused_extra = headline.replace('@@NAME@@', nation.title()).split('\n')
        option_summary = dict(option=option_text, datums=datums, net_result=weight, headline=headline, **first_row)
        option_list.append(option_summary)
        for row in extras:
            cols.extend(key for key in row if key not in cols)
            option_list.append(row)
    options = pandas.DataFrame.from_records(option_list)
    options['percent'] = probability_list(options['net_result'])
    options: pandas.DataFrame = options.reindex([col for col in cols if col in options.columns], axis=1)
    return scales_df, options

def probability_list(pd_series: pandas.Series):
    exponents: pandas.Series[float] = EXPONENT_BASE**pd_series
    exp_sum = sum(exp for exp in exponents if not math.isnan(exp))
    probability: pandas.Series[float] = exponents * 100 / exp_sum
    sort_func = lambda prob: abs(round(prob) - prob)
    probability.sort_values(key=sort_func, inplace=True)
    rounded_list: List[int] = []
    remainder = 100
    for prob in probability:
        if math.isnan(prob):
            rounded_list.append(prob)
            continue
        rounded_prob: int = round(prob)
        if rounded_prob < remainder:
            rounded_list.append(rounded_prob)
            remainder -= rounded_prob
        else:
            rounded_list.append(remainder)
            remainder = 0
    rounded = pandas.Series(rounded_list, index=probability.index).sort_index()
    return rounded

def weigh_option(effect_col: lxml.html.HtmlElement, count_col: lxml.html.HtmlElement):
    effects: List[str] = effect_col.text_content().strip().splitlines()
    counts: List[str] = count_col.text_content().strip().splitlines()
    counts += [''] * (len(effects) - len(counts))
    results: Dict[str, float] = {}
    unparsed_strs: List[str] = []
    min_count = None
    for effect_str, count_str in zip(effects, counts):
        if effect_str.startswith('unknown effect') or count_str == '1':
            continue
        count = int(count_str) if count_str.isdecimal() else 0
        if min_count is None or 0 < count < min_count:
            min_count = count
        regular = effect_pattern.search(effect_str)
        simple = simple_pattern.search(effect_str)
        if regular:
            category, delta = parse_regular_pattern(regular)
            results[category] = delta
        elif simple:
            delta_str, category = simple.groups()
            delta = float(delta_str)
            results[category] = (delta > 0) - (delta < 0)
        else:
            unparsed_strs.append(effect_str)
    return results, unparsed_strs, min_count

def parse_regular_pattern(regular: re.Match) -> Tuple[str, float]:
    low = float(regular.group(1))
    high = float(regular.group(2))
    census: str = regular.group(3)
    mean = float(regular.group(4))
    numer = min(high, 0) + mean + max(low, 0)
    denom = max(high, 0) - min(low, 0)
    delta = numer / 2 / denom
    return census, delta

def split_unparsed_strings(unparsed_strs: List[str]) -> List[Dict[str, str]]:
    records: List[Dict[str, str]] = []
    for extra in unparsed_strs:
        if ' policy: ' in extra:
            behavior, policy = extra.split(' policy: ', 1)
        elif ' notability: ' in extra:
            behavior, policy = extra.split(' notability: ', 1)
        elif extra.endswith(' the World Assembly'):
            behavior, policy = extra.rsplit(' the ', 1)
        elif extra.startswith('leads to '):
            behavior, policy = extra.rsplit(' ', 1)
        elif ' ' in extra:
            behavior, policy = extra.rsplit(' ', 1)
        elif extra:
            behavior = ''
            policy = extra
        else:
            continue
        for row in records:
            if behavior not in row:
                row[behavior] = policy
                break
        else:
            row = {behavior: policy}
            records.append(row)
    return records

if __name__ == '__main__':
    main(*sys.argv[1:])
