''' attempt to forecast best option from probable effects '''
import pathlib
import decimal
import sys
import re

import requests
import lxml.html
import pandas

float_grp = r'([-+]?\d+\.?\d*)'
effect_pattern = re.compile(r'{num} to {num} (.+) \(mean {num}\)'.format(num=float_grp))
simple_pattern = re.compile(r'{num} (.+)'.format(num=float_grp))

def get_options(nation=None, issue=None, print_bias='.4', result_thresh=None):
    if nation is None:
        nation = input('nation: ')
    if issue is None:
        issue = input('issue: ')

    scales_df, options = build_dataframes(nation, issue, result_thresh)

    scales_df.dropna(thresh=2, inplace=True)
    scales_df['sort'] = pandas.Series(abs(scales_df.bias) + (scales_df.bias > 0) / 200, scales_df.index)
    scales_df = scales_df.sort_values(by='sort', ascending=False).drop(['sort'], 1).fillna(0)
    with pandas.option_context('display.max_colwidth', -1):
        print('\n' + scales_df[abs(scales_df.bias) > decimal.Decimal(print_bias)].to_string())
        print('\n' + options.to_string(index=False))
    print('\nhttps://nsindex.net/wiki/NationStates_Issue_No._{issue}\n'.format(issue=issue))

def build_dataframes(nation, issue, result_thresh):
    scales_file = pathlib.Path(nation + '_category_scale.csv')
    assert scales_file.is_file(), str(scales_file)
    scales_df = pandas.read_csv(scales_file, names=('census', 'bias'), index_col='census')
    category_scales = scales_df.to_dict()['bias']

    policies_file = pathlib.Path(nation + '_policy_exclusions.csv')
    if policies_file.is_file():
        df = pandas.read_csv(policies_file, names=('policy', 'change')).dropna()
        exclusions = tuple('{change} policy: {policy}'.format(**row) for row in df.to_dict('records'))
    else:
        exclusions = ()

    url = 'http://www.mwq.dds.nl/ns/results/{issue}.html'
    page = requests.get(url.format(issue=issue), headers = {'content-type': 'text/html'})

    doc = lxml.html.fromstring(page.content)
    tr_elements = doc.xpath('//tr')
    options = pandas.DataFrame(columns=['option', 'net_result'])
    for result, effects, _ in tr_elements[1:]:
        choice, headline = result.text_content()[1:].split(' ', 1)
        deltas, unparsed_strs = weigh_option(effects)
        if any(excluded in unparsed_strs for excluded in exclusions):
            continue
        weight = sum(category_scales[category] * normalized_effect for category, normalized_effect in deltas)
        if result_thresh is not None and weight < float(result_thresh):
            continue
        scales_df[choice] = scales_df.index.map(dict(deltas))
        extras = split_unparsed_strings(unparsed_strs)
        headlines = headline.replace('@@NAME@@', nation.title()).split('\n')
        for headline in headlines:
            option_summary = dict(option=choice, net_result=weight, headline=headline, **extras)
            options = options.append(option_summary, ignore_index=True)
    return scales_df, options

def weigh_option(effects):
    results = []
    unparsed_strs = []
    for effect in effects:
        effect_str = effect.text_content()
        regular = effect_pattern.search(effect_str)
        simple = simple_pattern.search(effect_str)
        if regular:
            low, high, category, mean = (tryfloat(vl) for vl in regular.groups())
            numer = (high + mean + low)/3
            denom = (high if high > 0 else 0) - (low if low < 0 else 0)
            normalized_effect =  numer / denom
        elif simple:
            mean, category = simple.groups()
            normalized_effect = (float(mean) > 0) - (float(mean) < 0)
        else:
            unparsed_strs.append(effect_str)
            continue
        results.append((category, normalized_effect))
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

if __name__ == '__main__':
    get_options(*sys.argv[1:])
