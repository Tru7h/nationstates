''' attempt to forecast best option from probable effects '''
import logging
import pathlib
import sys
import re

import requests
import lxml.html
import pandas

ptrn_grps = dict(num=r'([-+]?\d+(?:\.\d+)?)', census=r'([^\.\d]+)')
effect_pattern = re.compile('^{num} to {num} {census} \(mean {num}\)$'.format(**ptrn_grps))
simple_pattern = re.compile('^{num} {census}$'.format(**ptrn_grps))
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

def get_options(nation: str=None, issue: str=None):
    while nation is None or not nation.isalnum():
        nation = input('nation: ')
    while issue is None or not issue.isdecimal():
        issue = input('issue: ')

    url = 'http://www.mwq.dds.nl/ns/results/{issue}.html'
    page = requests.get(url.format(issue=issue), headers = {'content-type': 'text/html'})
    census_filter = True

    doc = lxml.html.fromstring(page.content)
    excluded = set()
    cumsum = False

    while True:
        scales_df, options = build_dataframes(nation, doc, excluded)
        summarize_results(scales_df, options, census_filter, cumsum)
        logger.info('https://nsindex.net/wiki/NationStates_Issue_No._{issue}\n'.format(issue=issue))
        option = input(
            '"f" > toggle zero bias\n'
            '"c" > toggle cumulative summation\n'
            '"1-9" > drop/restore option\n'
            '"0" > reset options\n'
            '"" > exit\n'
            '>> ')
        if not option:
            break
        elif option == '0':
            excluded = set()
        elif option == 'f':
            census_filter = not census_filter
        elif option == 'c':
            cumsum = not cumsum
        elif option in excluded:
            excluded.remove(option)
        else:
            excluded.add(option)

def summarize_results(scales_df, options, census_filter, cumsum):
    scales_df.dropna(thresh=2, inplace=True)
    if not options.empty:
        scales_df['magnitude'] = pandas.Series(abs(scales_df.bias), scales_df.index)
        scales_df['direction'] = pandas.Series(scales_df.bias > 0, scales_df.index)
        sort_cols = ['magnitude', 'direction']
        viable_options = options[options.option!='']
        viable_options = options[:1] if viable_options.empty else viable_options
        for option in viable_options.sort_values(by='net_result', ascending=False).option:
            scales_df['abs_' + option] = pandas.Series(abs(scales_df[option]), scales_df.index)
            sort_cols.append('abs_' + option)
        scales_df.sort_values(by=sort_cols, ascending=False, inplace=True)
        scales_df = scales_df.drop(sort_cols, 1).fillna(0)
    if cumsum:
        bias_col, *option_cols = scales_df.columns
        for option in option_cols:
            scales_df[option] = (scales_df[bias_col] * scales_df[option]).cumsum()
    with pandas.option_context('display.max_colwidth', -1):
        scales_df = scales_df[scales_df.bias != 0] if census_filter else scales_df
        logger.info(scales_df.to_string())
        logger.info(options.to_string(index=False))

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

    title, = doc.xpath('//title')
    logger.info(title.text_content())
    cols = 'option,datums,net_result,headline'.split(',')
    options = pandas.DataFrame(columns=cols)
    for result, effects, observations in doc.xpath('//tr')[1:]:
        datums = observations.text_content().strip().splitlines() or '0'
        datums, *extras = set(cnt for cnt in datums if '-' not in cnt)
        assert not extras, observations.text_content()
        datums = int(datums)
        option_text, headline = result.text_content()[1:].split(' ', 1)
        if datums < 1 or any(digit in option_text for digit in excluded):
            continue
        deltas, unparsed_strs = weigh_option(effects)
        weight = sum(category_scales[category] * deltas[category] for category in deltas)
        scales_df[option_text] = [deltas.get(category) for category in scales_df.index]
        if any(reform in unparsed_strs for reform in excluded_policy_reforms):
            option = ''
        extras = split_unparsed_strings(unparsed_strs)
        cols.extend(key for key in extras if key not in cols)
        headlines = headline.replace('@@NAME@@', nation.title()).split('\n')
        for headline in headlines:
            option_summary = dict(option=option_text, datums=datums, net_result=weight, headline=headline, **extras)
            options = options.append(option_summary, ignore_index=True)
            option_text = ''
    return scales_df, options[cols]

def weigh_option(effects):
    results = {}
    unparsed_strs = []
    for effect in effects:
        effect_str = effect.text_content()
        regular = effect_pattern.search(effect_str)
        simple = simple_pattern.search(effect_str)
        if regular:
            category, delta = parse_regular_pattern(regular)
            results[category] = delta
        elif simple:
            mean, category = simple.groups()
            results[category] = (float(mean) > 0) - (float(mean) < 0)
        else:
            unparsed_strs.append(effect_str)
    return results, unparsed_strs

def parse_regular_pattern(regular):
    low = float(regular.group(1))
    high = float(regular.group(2))
    census = regular.group(3)
    mean = float(regular.group(4))
    numer = high + mean + low
    denom = max(high, 0) - min(low, 0)
    delta = numer / denom / 2
    return census, delta

def split_unparsed_strings(unparsed_strs):
    extras = {}
    extra: str
    for extra in unparsed_strs:
        if ' policy: ' in extra:
            behavior, policy = extra.split(' policy: ')
        elif extra.endswith(' the World Assembly'):
            behavior, policy = extra.split(' the ')
        else:
            behavior, policy = extra.rsplit(' ', 1)
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
