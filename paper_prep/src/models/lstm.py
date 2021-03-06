# -*- coding: utf-8 -*-
"""
largely from Jiaxuan


unit tests:
python lstm_model.py classification
python lstm_model.py regression
"""


import numpy as numpy
import tensorflow as tf
import sys
import numpy as np
# import matplotlib
# matplotlib.use('Agg')
# import matplotlib.pyplot as plt
from src.training.evaluation import accuracy





def conv1d(input_data, name='conv1d'):
    with tf.variable_scope(name):
        dims = input_data.get_shape()
        # TODO OTHER THAN 10 FILTERS??
        filters = tf.get_variable('f', [32 * 2, 10, 10], initializer=tf.contrib.layers.variance_scaling_initializer())
        conv_output = tf.nn.conv1d(input_data, filters, stride=1, padding='SAME')
        return conv_output


def conv_relu_batch(input_data, filter_dims, stride, conv_type="valid", name="crb"):

    def conv2d(name="conv2d"):
        with tf.variable_scope(name):
            W = tf.get_variable("W", filter_dims,
                    initializer=tf.contrib.layers.variance_scaling_initializer())
            b = tf.get_variable("b", [1, 1, 1, filter_dims[-1]])
            if conv_type == 'valid':    # if valid, one dot product
                return tf.nn.conv2d(input_data, W, [1, stride, stride, 1], "VALID") + b
            else:                       # if same, slide filters over it
                return tf.nn.conv2d(input_data, W, [1, stride, stride, 1], "SAME") + b

    def batch_normalization(input_data, axes=[0], name="batch"):
        with tf.variable_scope(name):
            mean, variance = tf.nn.moments(input_data, axes, keep_dims=True, name="moments")
            return tf.nn.batch_normalization(input_data, mean, variance, None, None, 1e-6, name="batch")

    with tf.variable_scope(name):
        a = conv2d()
        b = batch_normalization(a,axes=[0,1,2])
        r = tf.nn.relu(b)
        return tf.reduce_max(r, axis=2)


def run_affine(inputs, H, N=None, name="affine_layer"):
    if not N:
        N = inputs.get_shape()[-1]
    with tf.variable_scope(name):
        W = tf.get_variable("W", [N, H], initializer=tf.contrib.layers.xavier_initializer())
        b = tf.get_variable("b", [1, H], initializer=tf.constant_initializer(0))
        return tf.matmul(inputs, W) + b


def run_lstm(inputs, targets, lengths, config, keep_prob=1):
    cell = tf.contrib.rnn.LSTMCell(config.lstm_h, state_is_tuple=True)
    cell = tf.contrib.rnn.DropoutWrapper(cell, output_keep_prob=keep_prob)
    stacked_cell = tf.contrib.rnn.MultiRNNCell([cell] * config.layers, state_is_tuple=True)
    state = cell.zero_state(config.B, tf.float32)
    outputs, final_state = tf.nn.dynamic_rnn(cell,
                                             inputs,
                                             sequence_length=lengths,
                                             initial_state=state,
                                             time_major=True)
    final_outputs = outputs[-1]
    return final_outputs


#shape=(35, 32, 128)

class LSTM():
    def __init__(self, config, regression=False):
#        self.sess = sess
        self.config = config
#        with tf.variable_scope(name):
        self.x = tf.placeholder(tf.float32, [config.B, config.W, config.H, config.C], name='x')
        self.y = tf.placeholder(tf.float32, [config.B])
        self.l = tf.placeholder(tf.int32, [config.B])
        self.lr = tf.placeholder(tf.float32, [])
        self.keep_prob = tf.placeholder(tf.float32, [])

        self.global_step = tf.Variable(0, dtype=tf.int32, trainable=False, name='global_step')

        self.batch_size = config.B


        if self.config.model_type == 'conv_lstm':
            # [batch, in_height, in_width, in_channels]
            inputs = tf.transpose(self.x, [0, 2, 1, 3])
            if config.conv_type == '1d':
                inputs = tf.reshape(inputs, [config.B, -1, config.C])
                lstm_inputs = conv1d(inputs)
                lstm_inputs = tf.reshape(lstm_inputs, [config.B, config.W, config.H, config.C])
                lstm_inputs = tf.transpose(self.x, [2, 0, 1, 3])   # move time to first dimension
                dim = lstm_inputs.get_shape().as_list()
                lstm_inputs = tf.reshape(lstm_inputs, [dim[0], -1, dim[2]*dim[3]])  # concat bands for each image

            else:
                if config.conv_type == '2d':
                    filter_dims = [3, 3, config.C, config.num_lstm_filters]
                else:
                    filter_dims = [1, self.config.W, config.C, config.num_lstm_filters]                

                lstm_inputs = conv_relu_batch(inputs, filter_dims, 1, conv_type=config.conv_type)
                lstm_inputs = tf.transpose(lstm_inputs, [1, 0, 2])  # time to 1st dim
        else:
            inputs = tf.transpose(self.x, [2, 0, 1, 3])   # move time to first dimension
            dim = inputs.get_shape().as_list()
            inputs = tf.reshape(inputs, [dim[0], -1, dim[2]*dim[3]])  # concat bands for each image
            lstm_inputs = inputs

        lstm_out = run_lstm(lstm_inputs, self.y, self.l, config, keep_prob=self.keep_prob)
        fc1 = run_affine(lstm_out, config.dense, name='fc1')
        self.logits = tf.squeeze(run_affine(fc1, 1, name='logits'))

        if regression:
            self.y_final = self.logits
            self.loss_err = tf.nn.l2_loss(self.logits - self.y)
        else:
            self.y_final = tf.sigmoid(self.logits)
            self.loss_err = tf.reduce_mean(tf.nn.sigmoid_cross_entropy_with_logits(logits=self.logits, labels=self.y))

        self.loss_reg = tf.add_n([tf.nn.l2_loss(v) for v in tf.trainable_variables()])

        self.loss = self.loss_err #+ (config.l2 * self.loss_reg)

        self.train_op = tf.train.AdamOptimizer(self.lr).minimize(self.loss,
            global_step=self.global_step)



    def fit_and_predict(self, data, val_data, sess):
        def train_epoch(data):
            i = 0
            train_loss = 0
            while i + self.batch_size < len(data):
                batch = data[i: i + self.batch_size]
                x_batch, y_batch, l_batch = zip(*batch)
                _, loss = sess.run([self.train_op, self.loss], feed_dict={
                    self.x: x_batch,
                    self.y: y_batch,
                    self.l: l_batch,
                    self.lr: self.config.lr,
                    self.keep_prob: self.config.keep_prob
                })
                train_loss += loss
                i += self.batch_size
            train_loss /= (i / self.batch_size)
            return train_loss


        def predict(data):
            i = 0
            out_pred = []
            out_prob = []
            total_loss = 0.0
            while i + self.batch_size < len(data):
                batch = data[i: i + self.batch_size]
                x_batch, y_batch, l_batch = zip(*batch)
                loss, pred = sess.run([self.loss, self.y_final], feed_dict={
                    self.x: x_batch,
                    self.y: y_batch,
                    self.l: l_batch,
                    self.keep_prob: 1.0
                    })
                total_loss += loss
                out_prob += [x for x in pred]
                out_pred = out_pred + [1 if x > 0.5 else 0 for x in pred]
                i += self.batch_size

            # batches dont fit into data nicely, so get the last batch
            # this is SO hacky and gross and i'm completely disgusted with 
            # myself but here we are...  ¯\_(ツ)_/¯ (oh and im also ignoring
            # the loss from this little overhang. sigh.)
            # whoever's reading this....i'm so sorry will you ever find 
            # room in your heart for forgiveness\
            # !!!!!!! TODO -- REFACTOR !!!!!!!!!
            final_batch = data[-self.batch_size:]
            x_batch, y_batch, l_batch = zip(*final_batch)
            loss, pred = sess.run([self.loss, self.y_final], feed_dict={
                self.x: x_batch,
                self.y: y_batch,
                self.l: l_batch,
                self.keep_prob: 1.0
                })
            remainder = len(data) - i
            to_add = pred[-remainder:]
            out_prob = out_prob + list(to_add[:])
            out_pred = out_pred + [1 if x > 0.5 else 0 for x in to_add[:]]
            acc = accuracy(out_pred, zip(*data)[1])

            return out_prob, out_pred, total_loss / (i / self.batch_size), acc

        epoch = 0
        best_loss = float('inf')
        best_acc = -float('inf')

        prob, pred, loss, acc = predict(val_data)
        best_preds = pred, prob

        train_epoch(data)

        epochs = 1
        while loss < best_loss or acc > best_acc:
            best_loss = loss if loss < best_loss else best_loss
            if acc > best_acc:
                best_acc = acc
                best_preds = (pred, prob)

            prob, pred, loss, acc = predict(val_data)
#            print loss, acc, epochs
            train_epoch(data)
            epochs += 1
        best_pred, best_prob = best_preds      #sigh
        return best_prob, best_pred, epochs



if __name__ == '__main__':
    pass
    ### COMMENTED OUT BECAUSE ATLAS5 DOESN'T HAVE MATPLOTLIB
    # test_type = sys.argv[1]

    # sess = tf.Session()
    # config = Config()
    # model = LSTM(config, "model", test_type)
    # sess.run(tf.initialize_all_variables())

    # print 'INFO: running %s test' % test_type
    # dummy_x = np.random.rand(config.B, config.W, config.H, config.C)
    # dummy_y = np.random.randint(2, size=config.B) if test_type == 'classification' else np.random.rand(config.B)
    # print dummy_x.shape
    # losses = []
    # for i in range(1000):
    #     # model.state = model.cell.zero_state(config.B, tf.float32)
    #     if i % 100 == 0:
    #         config.lr /= 2
    #     _, loss, pred = sess.run([model.train_op, model.loss, model.y_final], feed_dict={
    #         model.x: dummy_x,
    #         model.y: dummy_y,
    #         model.lr: config.lr,
    #         model.keep_prob: config.keep_prob
    #     })

    #     losses.append(loss)

    # print 'INFO: plotting losses...'
    # plt.plot(range(len(losses)), losses)
    # plt.xlabel('Epochs')
    # plt.ylabel('total loss')
    # plt.title('loss')
    # plt.savefig('LOSSES_test.png')
    # plt.close()





