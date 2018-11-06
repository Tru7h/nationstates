''' attempt to forecast best option from probable effects '''
import decimal
import logging
import pathlib
import random
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

def get_options(nation=None, issue=None, minimum=None):
    if nation is None:
        nation = input('nation: ')
    if issue is None:
        issue = input('issue: ')
        if minimum is None:
            minimum = input('minimum: ') or None

    scales_df, options = build_dataframes(nation, issue, minimum)

    scales_df.dropna(thresh=2, inplace=True)
    scales_df['sort'] = pandas.Series(abs(scales_df.bias) + (scales_df.bias > 0) / 200, scales_df.index)
    scales_df = scales_df.sort_values(by='sort', ascending=False).drop(['sort'], 1).fillna(0)
    with pandas.option_context('display.max_colwidth', -1):
        logger.info(scales_df[abs(scales_df.bias) > 0].head(18).to_string())
        logger.info(options.to_string(index=False))
    logger.info('https://nsindex.net/wiki/NationStates_Issue_No._{issue}\n'.format(issue=issue))

def build_dataframes(nation, issue, minimum):
    scales_file = pathlib.Path(nation + '_category_scale.csv')
    assert scales_file.is_file(), str(scales_file)
    scales_df = pandas.read_csv(scales_file, names=('census', 'bias'), index_col='census')
    category_scales = scales_df.to_dict('dict')['bias']

    policies_file = pathlib.Path(nation + '_policy_exclusions.csv')
    if policies_file.is_file():
        df = pandas.read_csv(policies_file, names=('policy', 'change')).dropna()
        exclusions = tuple('{change} policy: {policy}'.format(**row) for row in df.to_dict('records'))
    else:
        exclusions = ()

    url = 'http://www.mwq.dds.nl/ns/results/{issue}.html'
    page = requests.get(url.format(issue=issue), headers = {'content-type': 'nation/achievement_hunters'})

    doc = lxml.html.fromstring(page.content)
    title, = doc.xpath('//title')
    logger.info(title.text_content())
    cols = 'option,datums,net_result,headline'.split(',')
    options = pandas.DataFrame(columns=cols)
    for result, effects, observations in doc.xpath('//tr')[1:]:
        datums = observations.text_content().strip().splitlines() or '0'
        datums = int(min(cnt for cnt in datums if '-' not in cnt))
        if datums < 1:
            continue
        choice, headline = result.text_content()[1:].split(' ', 1)
        deltas, unparsed_strs = weigh_option(effects)
        if any(excluded in unparsed_strs for excluded in exclusions):
            continue
        weight = sum(category_scales[category] * deltas[category] for category in deltas)
        if minimum is not None and weight < float(minimum):
            continue
        scales_df[choice] = [deltas.get(category) for category in scales_df.index]
        extras = split_unparsed_strings(unparsed_strs)
        cols.extend(key for key in extras if key not in cols)
        headlines = headline.replace('@@NAME@@', nation.title()).split('\n')
        for headline in headlines:
            option_summary = dict(option=choice, datums=datums, net_result=weight, headline=headline, **extras)
            options = options.append(option_summary, ignore_index=True)
            choice = ''
    return scales_df, options[cols]

def weigh_option(effects):
    results = {}
    unparsed_strs = []
    for effect in effects:
        effect_str = effect.text_content()
        regular = effect_pattern.search(effect_str)
        simple = simple_pattern.search(effect_str)
        if regular:
            low, high, category, mean = (tryfloat(vl) for vl in regular.groups())
            numer = high + mean + low
            denom = (high if high > 0 else 0) - (low if low < 0 else 0)
            results[category] = numer / denom / 3
        elif simple:
            mean, category = simple.groups()
            results[category] = (float(mean) > 0) - (float(mean) < 0)
        else:
            unparsed_strs.append(effect_str)
    return results, unparsed_strs

def tryfloat(string):
    try:
        return float(string)
    except ValueError:
        return string

def split_unparsed_strings(unparsed_strs):
    extras = {}
    for extra in unparsed_strs:
        if ' policy: ' in extra:
            behavior, policy = extra.split(' policy: ')
        else:
            behavior, policy = extra.rsplit(' ', 1)
        extras[policy] = behavior
    return extras

def main():
    c_handler = logging.StreamHandler()
    c_handler.setLevel(logging.INFO)
    c_format = logging.Formatter('\n%(message)s')
    c_handler.setFormatter(c_format)
    logger.addHandler(c_handler)
    logger.setLevel(logging.INFO)
    get_options(*sys.argv[1:])

if __name__ == '__main__':
    main()
