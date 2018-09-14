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

def get_options(arg, nation=None, issue=None, review_thresh='.84'):
    if nation is None:
        assert __name__ == '__main__'
        nation = input('nation: ')
    if issue is None:
        assert __name__ == '__main__'
        issue = input('issue: ')
    if review_thresh:
        thresh = decimal.Decimal(review_thresh)
    scales_file = pathlib.Path(nation + '_category_scale.csv')
    assert scales_file.is_file(), str(scales_file)
    scales_df = pandas.read_csv(scales_file, header=None, index_col=False)
    category_scales = dict(scales_df.itertuples(index=False))
    scales_df.columns=('census', 'bias')

    url = 'http://www.mwq.dds.nl/ns/results/{issue}.html'
    page = requests.get(url.format(issue=issue), headers = {'content-type': 'text/html'})

    doc = lxml.html.fromstring(page.content)
    tr_elements = doc.xpath('//tr')
    options = pandas.DataFrame(columns=['option', 'net result'])
    for option, effects, _ in tr_elements[1:]:
        if thresh <= max(scales_df.bias):
            print('\nWeighing option {num}'.format(num=option.text_content()[1:]))
        weight, extras = weigh_option(effects, category_scales, thresh)
        option_summary = {'option': option.text_content()[1:].split(' ', 1)[0], 'net result': weight, **extras}
        options = options.append(option_summary, ignore_index=True)

    with pandas.option_context('display.max_colwidth', -1):
        print('\n' + options.to_string(index=False))

def weigh_option(effects, category_scales, thresh):
    results = []
    unparsed_strs = []
    for effect in effects:
        effect_str = effect.text_content()
        regular = effect_pattern.search(effect_str)
        simple = simple_pattern.search(effect_str)
        if regular:
            low, high, category, mean = regular.groups()
            numer = sum(float(vl) for vl in (high, mean, low))/3
            denom = float(high) - float(low)
            normalized_effect =  numer / denom
        elif simple:
            mean, category = simple.groups()
            normalized_effect = (float(mean) > 0) - (float(mean) < 0)
        else:
            unparsed_strs.append(effect_str)
            continue
        results.append((category, normalized_effect, category_scales[category]))
    net_result = summarize_results(results, thresh)
    extras = split_unparsed_strings(unparsed_strs)
    return net_result, extras

def summarize_results(results, thresh):
    net_result = 0
    sorted_results = sorted(results, key=lambda effect: (-abs(effect[2]), effect[2] < 0))
    for category, normalized_effect, bias in sorted_results:
        delta = normalized_effect * bias
        net_result += delta
        if abs(bias) < thresh:
            continue
        print('{category}: {delta:.4f}'.format(category=category, delta=delta))
    return net_result

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
    get_options(*sys.argv)
