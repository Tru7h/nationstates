''' attempt to forecast best option from probable effects '''
import logging
import pathlib
import random
import sys
import re

import requests
import lxml.html
import pandas

EXPONENT_BASE = 11.3 # float greater than one
REQUEST_HEADERS = {'content-type': 'text/html'}
ptrn_grps = dict(num=r'([-+]?\d+(?:\.\d+)?)', census=r'([^\.\d]+)')
effect_pattern = re.compile('^{num} to {num} {census} \(mean {num}\)$'.format(**ptrn_grps))
simple_pattern = re.compile('^{num} {census}$'.format(**ptrn_grps))
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

def get_options(nation: str=None, issue: str=None):
    while nation is None or not nation.isalnum():
        nation = input('nation: ')
        scales_file = pathlib.Path(nation + '_category_scale.csv')
        if scales_file.is_file():
            break
        logger.error('Category csv for nationstate: "%s" not found.', nation)
        nation = None
    while issue is None or not issue.isdecimal():
        issue = input('issue: ')
        if issue != '?':
            continue
        break
    option = 'n ' + issue

    while True:
        match = re.search('n (\d{1,4}|\?)', option)
        if match:
            issue, doc = get_issue(match)
            census_filter = True
            excluded = set()
            cumsum = False
        elif option == '0':
            excluded = set()
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
        scales_df, options = build_dataframes(nation, doc, excluded)
        summarize_results(scales_df, options, census_filter, cumsum)
        logger.info('https://nsindex.net/wiki/NationStates_Issue_No._{issue}\n'.format(issue=issue))
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

def get_issue(match):
    url = 'http://www.mwq.dds.nl/ns/results/'
    issue, *extras = match.groups()
    assert not extras
    if issue == '?':
        page = requests.get(url, headers=REQUEST_HEADERS)
        doc = lxml.html.fromstring(page.content)
        regex = re.findall('#(\d+) [^\?]', doc.text_content())
        issue = random.choice(regex)

    page = requests.get(url + f'{issue}.html', headers=REQUEST_HEADERS)
    doc = lxml.html.fromstring(page.content)
    return issue, doc

def summarize_results(scales_df, options, census_filter, cumsum):
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
        scales_df_T = scales_df.T
        for index in scales_df_T:
            bias, *option_nets = scales_df_T[index]
            prob_list = [bias] + probability_list(option_nets)
            new_row = pandas.Series(prob_list, index=scales_df_T.index)
            scales_df_T[index] = new_row
        scales_df = scales_df_T.T[[bias_col] + option_cols]
        for option in option_cols:
            scales_df[option] = scales_df[option].astype(int)
    with pandas.option_context('display.max_colwidth', -1):
        scales_df = scales_df[scales_df.bias != 0] if census_filter else scales_df
        logger.info(scales_df.to_string())
        logger.info(options.to_string(index=False))
    return

def build_dataframes(nation, doc, excluded):
    scales_file = pathlib.Path(nation + '_category_scale.csv')
    assert scales_file.is_file(), scales_file.name
    scales_df = pandas.read_csv(scales_file, names=('census', 'bias'), index_col='census')
    category_scales = scales_df.to_dict('dict')['bias']

    policies_file = pathlib.Path(nation + '_policy_exclusions.csv')
    if policies_file.is_file():
        df = pandas.read_csv(policies_file, names=('policy', 'change')).dropna().to_dict('records')
        excluded_policy_reforms = tuple('{change} policy: {policy}'.format(**row) for row in df)
    else:
        excluded_policy_reforms = ()

    title, *extras = doc.xpath('//title')
    assert not extras
    logger.info(title.text)
    cols = 'option,datums,net_result,percent,headline'.split(',')
    option_summary = dict(option='0.', datums=None, net_result=0, headline='Dismiss issue.')
    options = [option_summary]
    for result, effects, observations in doc.xpath('//tr')[1:]:
        option_text, headline = result.text_content()[1:].split(' ', 1)
        deltas, unparsed_strs, datums = weigh_option(effects, observations)
        if any(option_text.startswith(option_str) for option_str in excluded):
            weight = -float('inf')
            extras = {}
        else:
            scales_df[option_text] = [deltas.get(category) for category in scales_df.index]
            weight = sum(category_scales[category] * deltas[category] for category in deltas)
            extras = split_unparsed_strings(unparsed_strs)
            if any(reform in unparsed_strs for reform in excluded_policy_reforms):
                option_text += ' policy reform'
        cols.extend(key for key in extras if key not in cols)
        headlines = headline.replace('@@NAME@@', nation.title()).split('\n')
        for headline in headlines:
            option_summary = dict(option=option_text, datums=datums, net_result=weight, headline=headline, **extras)
            options.append(option_summary)
            weight = -float('inf')
            option_text = ''
    options = pandas.DataFrame.from_records(options)
    percent = probability_list(options.net_result)
    options['percent'] = pandas.Series(percent, index=options.index)
    return scales_df, options[cols]

def probability_list(pd_series):
    exponents = [EXPONENT_BASE**net for net in pd_series]
    probability = [exp/sum(exponents) for exp in exponents]
    return [round(prob*99) for prob in probability]

def weigh_option(effect_col, count_col):
    effects = effect_col.text_content().strip().splitlines()
    counts = count_col.text_content().strip().splitlines()
    counts += [''] * (len(effects) - len(counts))
    results = {}
    unparsed_strs = []
    min_count = None
    for effect_str, count_str in zip(effects, counts):
        if effect_str.startswith('unknown effect') or count_str == '1':
            continue
        count = int(count_str) if count_str.isdecimal() else 0
        min_count = count if not min_count or 0 < count < min_count else min_count
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

def parse_regular_pattern(regular):
    low = float(regular.group(1))
    high = float(regular.group(2))
    census = regular.group(3)
    mean = float(regular.group(4))
    numer = min(high, 0) + mean + max(low, 0)
    denom = max(high, 0) - min(low, 0)
    delta = numer / denom
    return census, delta

def split_unparsed_strings(unparsed_strs):
    extras = {}
    extra: str
    for extra in unparsed_strs:
        if ' policy: ' in extra:
            behavior, policy = extra.split(' policy: ', 1)
        elif ' notability: ' in extra:
            behavior, policy = extra.split(' notability: ', 1)
        elif extra.endswith(' the World Assembly'):
            behavior, policy = extra.rsplit(' the ', 1)
        elif extra.startswith('leads to '):
            policy, behavior = extra.rsplit(' ', 1)
        elif ' ' in extra:
            behavior, policy = extra.rsplit(' ', 1)
        elif extra:
            behavior = ''
            policy = extra
        extras[policy] = behavior
    return extras

def main(level=logging.INFO):
    c_handler = logging.StreamHandler()
    c_handler.setLevel(level)
    c_format = logging.Formatter('\n%(message)s')
    c_handler.setFormatter(c_format)
    logger.addHandler(c_handler)
    logger.setLevel(level)
    get_options(*sys.argv[1:])

if __name__ == '__main__':
    main()
