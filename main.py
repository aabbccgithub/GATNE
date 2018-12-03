import os
import sys
import logging
import math
import argparse
import subprocess
import tqdm
import time
import numpy as np
from numpy import random
import tensorflow as tf

from collections import defaultdict
from six import iteritems
from sklearn.metrics import roc_auc_score
from gensim.models import Word2Vec
from gensim.models.keyedvectors import Vocab

import Random_walk
from utils import *

def parse_args():
    parser = argparse.ArgumentParser()

    parser.add_argument('--input', nargs='?', default='graph/karate.edgelist',
                        help='Input graph path')
    
    parser.add_argument('--features', nargs='?', default=None,
                        help='Input node features (npy)')

    parser.add_argument('--output', nargs='?', default='emb/karate.emb',
                        help='Embeddings path')

    parser.add_argument('--epoch', type=int, default=5,
                        help='Number of epoch. Default is 5.')

    parser.add_argument('--batch_size', type=int, default=64,
                        help='Number of batch_size. Default is 64.')

    parser.add_argument('--dimensions', type=int, default=200,
                        help='Number of dimensions. Default is 200.')

    parser.add_argument('--walk-length', type=int, default=10,
                        help='Length of walk per source. Default is 10.')

    parser.add_argument('--num-walks', type=int, default=20,
                        help='Number of walks per source. Default is 20.')

    parser.add_argument('--window-size', type=int, default=5,
                        help='Context size for optimization. Default is 5.')

    parser.add_argument('--iter', type=int, default=10,
                        help='Number of epochs in SGD')

    parser.add_argument('--workers', type=int, default=8,
                        help='Number of parallel workers. Default is 8.')

    parser.add_argument('--p', type=float, default=1,
                        help='Return hyperparameter. Default is 1.')

    parser.add_argument('--q', type=float, default=1,
                        help='Inout hyperparameter. Default is 1.')

    parser.add_argument('--weighted', dest='weighted', action='store_true',
                        help='Boolean specifying (un)weighted. Default is unweighted.')
    parser.add_argument('--unweighted', dest='unweighted', action='store_false')
    parser.set_defaults(weighted=False)

    parser.add_argument('--directed', dest='directed', action='store_true',
                        help='Graph is (un)directed. Default is undirected.')
    parser.add_argument('--undirected', dest='undirected', action='store_false')
    parser.set_defaults(directed=False)

    return parser.parse_args()


# randomly divide data into few parts for the purpose of cross-validation
def divide_data(input_list, group_number):
    local_division = len(input_list) / float(group_number)
    random.shuffle(input_list)
    return [input_list[int(round(local_division * i)): int(round(local_division * (i + 1)))] for i in
            range(group_number)]


def randomly_choose_false_edges(nodes, true_edges, num):
    true_edges_set = set(true_edges)
    tmp_list = list()
    all_flag = False
    for _ in range(num):
        trial = 0
        while True:
            x = nodes[random.randint(0, len(nodes))]
            y = nodes[random.randint(0, len(nodes))]
            trial += 1
            if trial >= 1000:
                all_flag = True
                break
            if x != y and (x, y) not in true_edges_set and (y, x) not in true_edges_set:
                tmp_list.append((x, y))
                break
        # print(_, x, y)
        if all_flag:
            break
    return tmp_list

def get_dict_neighbourhood_score(local_model, node1, node2):
    try:
        vector1 = local_model[node1]
        vector2 = local_model[node2]
        return np.dot(vector1, vector2) / (np.linalg.norm(vector1) * np.linalg.norm(vector2))
    except:
        return 2+random.random()


def get_dict_AUC(model, true_edges, false_edges):
    true_list = list()
    prediction_list = list()
    for edge in true_edges:
        tmp_score = get_dict_neighbourhood_score(model, str(edge[0]), str(edge[1]))
        true_list.append(1)
        # prediction_list.append(tmp_score)
        # for the unseen pair, we randomly give a prediction
        if tmp_score > 2:
            if tmp_score > 2.5:
                prediction_list.append(1)
            else:
                prediction_list.append(-1)
        else:
            prediction_list.append(tmp_score)
    for edge in false_edges:
        tmp_score = get_dict_neighbourhood_score(model, str(edge[0]), str(edge[1]))
        true_list.append(0)
        # prediction_list.append(tmp_score)
        # for the unseen pair, we randomly give a prediction
        if tmp_score > 2:
            if tmp_score > 2.5:
                prediction_list.append(1)
            else:
                prediction_list.append(-1)
        else:
            prediction_list.append(tmp_score)
    y_true = np.array(true_list)
    y_scores = np.array(prediction_list)
    # print(y_true)
    # print(y_scores)
    return roc_auc_score(y_true, y_scores)

def get_neighbourhood_score(local_model, node1, node2):
    try:
        vector1 = local_model.wv.vectors[local_model.wv.index2word.index(node1)]
        vector2 = local_model.wv.vectors[local_model.wv.index2word.index(node2)]
        return np.dot(vector1, vector2) / (np.linalg.norm(vector1) * np.linalg.norm(vector2))
    except:
        return 2+random.random()

def get_AUC(model, true_edges, false_edges):
    true_list = list()
    prediction_list = list()
    for edge in true_edges:
        tmp_score = get_neighbourhood_score(model, str(edge[0]), str(edge[1]))
        true_list.append(1)
        # prediction_list.append(tmp_score)
        # for the unseen pair, we randomly give a prediction
        if tmp_score > 2:
            if tmp_score > 2.5:
                prediction_list.append(1)
            else:
                prediction_list.append(-1)
        else:
            prediction_list.append(tmp_score)

    for edge in false_edges:
        tmp_score = get_neighbourhood_score(model, str(edge[0]), str(edge[1]))
        true_list.append(0)
        # prediction_list.append(tmp_score)
        # for the unseen pair, we randomly give a prediction
        if tmp_score > 2:
            if tmp_score > 2.5:
                prediction_list.append(1)
            else:
                prediction_list.append(-1)
        else:
            prediction_list.append(tmp_score)
    y_true = np.array(true_list)
    y_scores = np.array(prediction_list)
    return roc_auc_score(y_true, y_scores)

def generate_pairs(all_walks, vocab):
    pairs = []
    skip_window = 2
    for layer_id, walks in enumerate(all_walks):
        for walk in walks:
            for i in range(len(walk)):
                for j in range(1, skip_window + 1):
                    if i - j >= 0:
                        pairs.append((vocab[walk[i]].index, vocab[walk[i - j]].index, layer_id))
                    if i + j < len(walk):
                        pairs.append((vocab[walk[i]].index, vocab[walk[i + j]].index, layer_id))
    return pairs

def generate_vocab(all_walks):
    index2word = []
    raw_vocab = defaultdict(int)

    # print(all_walks)

    for walks in all_walks:
        for walk in walks:
            for word in walk:
                raw_vocab[word] += 1
    # print(raw_vocab)

    vocab = {}
    for word, v in iteritems(raw_vocab):
        vocab[word] = Vocab(count=v, index=len(index2word))
        index2word.append(word)

    index2word.sort(key=lambda word: vocab[word].count, reverse=True)
    for i, word in enumerate(index2word):
        vocab[word].index = i
    
    # print(index2word)
    # print([(word, vocab[word].count, vocab[word].index) for word in vocab.keys()])

    return vocab, index2word

def get_batches(pairs, batch_size):
    n_batches = (len(pairs) + (batch_size - 1)) // batch_size

    result = []
    for idx in range(n_batches):
        x, y, t = [], [], []
        for i in range(batch_size):
            index = idx * batch_size + i
            if index >= len(pairs):
                break
            x.append(pairs[index][0])
            y.append(pairs[index][1])
            t.append(pairs[index][2])
        result.append((np.array(x).astype(np.int32), np.array(y).reshape(-1, 1).astype(np.int32), np.array(t).astype(np.int32)))
    return result

def train_deepwalk_embedding(walks, iteration=None):
    if iteration is None:
        iteration = 10 # original iter 100
    model = Word2Vec(walks, size=200, window=5, min_count=0, sg=1, workers=5, iter=iteration)
    return model

def train_model(network_data, feature_dic, log_name):
    base_network = network_data['Base']
    base_G = Random_walk.RWGraph(get_G_from_edges(base_network), False, 1, 1)
    print('finish building the graph')
    base_G.preprocess_transition_probs()
    base_walks = base_G.simulate_walks(20, 10)
    # all_walks = [base_walks]
    all_walks = []
    edge_types = list(network_data.keys())
    for layer_id in network_data:
        if layer_id == 'Base':
            continue

        tmp_data = network_data[layer_id]
        # start to do the random walk on a layer
        layer_G = Random_walk.RWGraph(get_G_from_edges(tmp_data), False, 1, 1)
        layer_G.preprocess_transition_probs()
        layer_walks = layer_G.simulate_walks(20, 10)
        all_walks.append(layer_walks)


    print('finish generating the walks')

    vocab, index2word = generate_vocab([base_walks])

    # node2vec_model = train_deepwalk_embedding(all_walks[0])

    train_pairs_base = generate_pairs([base_walks], vocab)
    train_pairs = generate_pairs(all_walks, vocab)

    num_nodes = len(index2word)
    edge_type_count = len(all_walks)
    epochs = args.epoch
    batch_size = args.batch_size
    embedding_size = 200 # Dimension of the embedding vector.
    embedding_u_size = 10
    num_sampled = 5 # Number of negative examples to sample.

    graph = tf.Graph()

    if feature_dic:
        feature_dim = len(list(feature_dic.values())[0])
        print('feature dimension: ' + str(feature_dim))
        features = np.zeros((num_nodes, feature_dim), dtype=np.float32)
        for key, value in feature_dic.items():
            if key in index2word:
                features[index2word.index(key), :] = np.array(value)
        

    with graph.as_default():
        global_step = tf.Variable(0, name='global_step', trainable=False)

        if feature_dic:
            node_features = tf.Variable(features, name='node_features', trainable=False)
            feature_weights = tf.Variable(tf.truncated_normal([feature_dim, embedding_size], stddev=1.0))

        # Parameters to learn
        node_embeddings = tf.Variable(tf.random_uniform([num_nodes, embedding_size], -1.0, 1.0))
        node_type_embeddings = tf.Variable(tf.random_uniform([edge_type_count * num_nodes, embedding_u_size], -1.0, 1.0))
        trans_weights = tf.Variable(tf.truncated_normal([edge_type_count, embedding_u_size, embedding_size], stddev=1.0 / math.sqrt(embedding_size)))
        nce_weights = tf.Variable(tf.truncated_normal([num_nodes, embedding_size], stddev=1.0 / math.sqrt(embedding_size)))
        nce_biases = tf.Variable(tf.zeros([num_nodes]))

        # Input data and re-orgenize size.
        train_inputs = tf.placeholder(tf.int32, shape=[None])
        train_labels = tf.placeholder(tf.int32, shape=[None, 1])
        train_types = tf.placeholder(tf.int32, shape=[None])
        
        # Look up embeddings for words.
        node_embed = tf.nn.embedding_lookup(node_embeddings, train_inputs)
        node_type_embed = tf.reshape(tf.nn.embedding_lookup(node_type_embeddings, train_inputs + train_types * num_nodes), [-1, 1, embedding_u_size])
        trans_w = tf.nn.embedding_lookup(trans_weights, train_types)
        # Compute the softmax loss, using a sample of the negative labels each time.
        # loss_node2vec = tf.reduce_mean(tf.nn.sampled_softmax_loss(softmax_weights, softmax_biases,node_embed, train_labels, num_sampled, num_nodes))

        if feature_dic:
            node_feat = tf.nn.embedding_lookup(node_features, train_inputs)
            node_embed = node_embed + 0.5 * tf.matmul(node_feat, feature_weights)

        loss_base = tf.reduce_mean(
            tf.nn.nce_loss(
                weights=nce_weights,
                biases=nce_biases,
                labels=train_labels,
                inputs=tf.nn.l2_normalize(node_embed, axis = 1),
                num_sampled=num_sampled,
                num_classes=num_nodes))
        loss = tf.reduce_mean(
            tf.nn.nce_loss(
                weights=nce_weights,
                biases=nce_biases,
                labels=train_labels,
                inputs=tf.nn.l2_normalize(node_embed + 0.5 * tf.reshape(tf.matmul(node_type_embed, trans_w), [-1, embedding_size]), axis = 1),
                num_sampled=num_sampled,
                num_classes=num_nodes))
        plot_loss_base = tf.summary.scalar("loss_base", loss_base)
        plot_loss = tf.summary.scalar("loss", loss)

        # Optimizer.
        optimizer_base = tf.train.AdamOptimizer().minimize(loss_base, global_step=global_step)
        optimizer = tf.train.AdamOptimizer().minimize(loss, global_step=global_step)

        # Add ops to save and restore all the variables.
        # saver = tf.train.Saver(max_to_keep=20)

        # merged = tf.summary.merge_all()

        # Initializing the variables
        init = tf.global_variables_initializer()

    # Launch the graph
    print("Optimizing")
    with tf.Session(graph=graph) as sess:
        log_dir = "./log/" 
        writer = tf.summary.FileWriter("./runs/" + log_name, sess.graph) # tensorboard --logdir=./runs
        sess.run(init)

        # print(sess.run(node_embeddings))
        print('Base Training')
        iter = 0
        for epoch in range(epochs):
            random.shuffle(train_pairs_base)
            batches = get_batches(train_pairs_base, batch_size)

            data_iter = tqdm.tqdm(enumerate(batches),
                                desc="EP:%d" % (epoch),
                                total=len(batches),
                                bar_format="{l_bar}{r_bar}")
            avg_loss = 0.0

            for i, data in data_iter:
                feed_dict = {train_inputs: data[0], train_labels: data[1], train_types: data[2]}
                _, loss_value, summary_str = sess.run([optimizer_base, loss_base, plot_loss_base], feed_dict)
                writer.add_summary(summary_str, iter)
                iter += 1

                avg_loss += loss_value

                if i % 1000 == 0:
                    # tmp_node_embeddings = sess.run(node_embeddings)
                    # if feature_dic:
                    #     tmp_feature_weights = sess.run(feature_weights)
                    # else:
                    #     tmp_feature_weights = None
                    # auc = evaluate(tmp_node_embeddings, tmp_feature_weights)

                    post_fix = {
                        "epoch": epoch,
                        "iter": i,
                        "avg_loss": avg_loss / (i + 1),
                        "loss": loss_value
                    }
                    data_iter.write(str(post_fix))

        print('Training for Each Type')
        iter = 0
        for epoch in range(epochs):
            random.shuffle(train_pairs)
            batches = get_batches(train_pairs, batch_size)

            data_iter = tqdm.tqdm(enumerate(batches),
                                desc="EP:%d" % (epoch),
                                total=len(batches),
                                bar_format="{l_bar}{r_bar}")
            avg_loss = 0.0

            for i, data in data_iter:
                feed_dict = {train_inputs: data[0], train_labels: data[1], train_types: data[2]}
                _, loss_value, summary_str = sess.run([optimizer, loss, plot_loss], feed_dict)
                writer.add_summary(summary_str, iter)
                iter += 1

                avg_loss += loss_value

                if i % 1000 == 0:
                    post_fix = {
                        "epoch": epoch,
                        "iter": i,
                        "avg_loss": avg_loss / (i + 1),
                        "loss": loss_value
                    }
                    data_iter.write(str(post_fix))

        final_node_embeddings, final_type_embeddings, final_trans_weights = sess.run([node_embeddings, node_type_embeddings, trans_weights])
        if feature_dic:
            final_feature_weights = sess.run(feature_weights)
    
    # np_node_embeddings = node2vec_model.wv.vectors
    # index2word = node2vec_model.wv.index2word
    # print(index2word)

    final_model = dict()
    # print(final_node_embeddings)
    final_model['base'] = final_node_embeddings
    final_model['index2word'] = index2word
    if feature_dic:
        final_model['fea_tran'] = final_feature_weights

    final_model['addition'] = {}
    final_model['tran'] = {}
    for edge_type in range(edge_type_count):
        final_model['addition'][edge_types[edge_type]] = final_type_embeddings[edge_type * num_nodes: (edge_type + 1) * num_nodes,:]
        final_model['tran'][edge_types[edge_type]] = final_trans_weights[edge_type,:,:]

    # def plot_with_labels(low_dim_embs, labels, filename):
    #     assert low_dim_embs.shape[0] >= len(labels), 'More labels than embeddings'
    #     plt.figure(figsize=(18, 18))  # in inches
    #     for i, label in enumerate(labels):
    #         x, y = low_dim_embs[i, :]
    #         plt.scatter(x, y)
    #         plt.annotate(
    #             label,
    #             xy=(x, y),
    #             xytext=(5, 2),
    #             textcoords='offset points',
    #             ha='right',
    #             va='bottom')

    #     plt.savefig(filename)

    # try:
    #     from sklearn.manifold import TSNE
    #     import matplotlib.pyplot as plt

    #     tsne = TSNE(
    #         perplexity=30, n_components=2, init='pca', n_iter=1000, method='barnes_hut')
    #     plot_only = min(num_nodes, 500)
    #     low_dim_embs = tsne.fit_transform(final_node_embeddings[:plot_only, :])
    #     labels = [index2word[i] for i in range(plot_only)]
    #     plot_with_labels(low_dim_embs, labels, os.path.join('./', log_name + '_tsne.png'))
    # except ImportError as ex:
    #     print('Please install sklearn, matplotlib, and scipy to show embeddings.')
    #     print(ex)
    
    # return node2vec_model

    return final_model

if __name__ == "__main__":
    args = parse_args()
    # logging.basicConfig(format='%(asctime)s : %(levelname)s : %(message)s', level=logging.INFO)
    file_name = args.input
    print(file_name)
    edge_data_by_type, _, all_nodes = load_network_data(file_name)
    if args.features:
        feature_dic = np.load(args.features)[()]
    else:
        feature_dic = None

    # In our experiment, we use 5-fold cross-validation, but you can change that
    number_of_groups = 5
    edge_data_by_type_by_group = dict()
    for edge_type in edge_data_by_type:
        all_data = edge_data_by_type[edge_type]
        separated_data = divide_data(all_data, number_of_groups)
        edge_data_by_type_by_group[edge_type] = separated_data

    overall_MNE_performance = list()

    for i in range(number_of_groups):
        training_data_by_type = dict()
        evaluation_data_by_type = dict()
        for edge_type in edge_data_by_type_by_group:
            training_data_by_type[edge_type] = list()
            evaluation_data_by_type[edge_type] = list()
            for j in range(number_of_groups):
                if j == i:
                    for tmp_edge in edge_data_by_type_by_group[edge_type][j]:
                        evaluation_data_by_type[edge_type].append((tmp_edge[0], tmp_edge[1]))
                else:
                    for tmp_edge in edge_data_by_type_by_group[edge_type][j]:
                        training_data_by_type[edge_type].append((tmp_edge[0], tmp_edge[1]))
        base_edges = list()
        training_nodes = list()
        for edge_type in training_data_by_type:
            for edge in training_data_by_type[edge_type]:
                base_edges.append(edge)
                training_nodes.append(edge[0])
                training_nodes.append(edge[1])
        training_nodes = list(set(training_nodes))
        training_data_by_type['Base'] = base_edges
        MNE_model = train_model(training_data_by_type, feature_dic, file_name.split('/')[-1].split('.')[0] + '_' + str(i) + '_' + time.strftime('%Y-%m-%d %H:%M:%S',time.localtime(time.time())))

        tmp_MNE_performance = 0

        merged_networks = dict()
        merged_networks['training'] = dict()
        merged_networks['test_true'] = dict()
        merged_networks['test_false'] = dict()
        for edge_type in training_data_by_type:
            if edge_type == 'Base':
                continue
            print('We are working on edge:', edge_type)
            selected_true_edges = list()
            tmp_training_nodes = list()
            for edge in training_data_by_type[edge_type]:
                tmp_training_nodes.append(edge[0])
                tmp_training_nodes.append(edge[1])
            tmp_training_nodes = set(tmp_training_nodes)
            for edge in evaluation_data_by_type[edge_type]:
                if edge[0] in tmp_training_nodes and edge[1] in tmp_training_nodes:
                    if edge[0] == edge[1]:
                        continue
                    selected_true_edges.append(edge)
            if len(selected_true_edges) == 0:
                continue
            selected_false_edges = randomly_choose_false_edges(training_nodes, edge_data_by_type[edge_type], len(selected_true_edges) * 5)
            print('number of info network edges:', len(training_data_by_type[edge_type]))
            print('number of evaluation true edges:', len(selected_true_edges))
            print('number of evaluation false edges:', len(selected_false_edges))
            merged_networks['training'][edge_type] = set(training_data_by_type[edge_type])
            merged_networks['test_true'][edge_type] = selected_true_edges
            merged_networks['test_false'][edge_type] = selected_false_edges

            local_model = dict()
            for pos in range(len(MNE_model['index2word'])):
                # 0.5 is the weight parameter mentioned in the paper, which is used to show how important each relation type is and can be tuned based on the network.
                node = MNE_model['index2word'][pos]
                local_model[node] = MNE_model['base'][pos] + 0.5 * np.dot(MNE_model['addition'][edge_type][pos], MNE_model['tran'][edge_type])
                if feature_dic:
                    local_model[node] = local_model[node] + 0.5 * np.dot(feature_dic[node], MNE_model['fea_tran'])
                # local_model[MNE_model['index2word'][pos]] = MNE_model['base'][pos]
            tmp_MNE_score = get_dict_AUC(local_model, selected_true_edges, selected_false_edges)
            # tmp_MNE_score = get_AUC(MNE_model['addition'][edge_type], selected_true_edges, selected_false_edges)
            
            tmp_MNE_performance += tmp_MNE_score
            print('MNE score:', tmp_MNE_score)

        print('MNE performance:', tmp_MNE_performance / (len(training_data_by_type)-1))
       
        overall_MNE_performance.append(tmp_MNE_performance / (len(training_data_by_type)-1))

    overall_MNE_performance = np.asarray(overall_MNE_performance)
   
    print('Overall MNE AUC:', overall_MNE_performance)

    print('')

    print('Overall MNE AUC:', np.mean(overall_MNE_performance))

    print('')

    print('Overall MNE std:', np.std(overall_MNE_performance))

    print('end')
