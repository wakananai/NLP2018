import sys, argparse, os
import keras
import _pickle as pk
import readline
import numpy as np
from sklearn.metrics import mean_squared_error,f1_score

from keras import regularizers
from keras.models import Model
from keras.layers import Input, Dense, Dropout, Conv1D, Flatten
from keras.layers.embeddings import Embedding
from keras.optimizers import Adam
from keras.callbacks import EarlyStopping, ModelCheckpoint

import keras.backend.tensorflow_backend as K
import tensorflow as tf

from utils.util import DataManager
import json

parser = argparse.ArgumentParser(description='Sentiment classification')
parser.add_argument('model')
parser.add_argument('action', choices=['train','test','semi'])

# training argument
parser.add_argument('--batch_size', default=128, type=float)
parser.add_argument('--nb_epoch', default=20, type=int)
parser.add_argument('--val_ratio', default=0.1, type=float)
parser.add_argument('--gpu_fraction', default=0.3, type=float)
parser.add_argument('--vocab_size', default=20000, type=int)
parser.add_argument('--max_length', default=40,type=int)

# model parameter
parser.add_argument('--loss_function', default='mse')
parser.add_argument('--cell', default='LSTM', choices=['LSTM','GRU'])
parser.add_argument('-emb_dim', '--embedding_dim', default=128, type=int)
parser.add_argument('-hid_siz', '--hidden_size', default=512, type=int)
parser.add_argument('--dropout_rate', default=0.3, type=float)
parser.add_argument('-lr','--learning_rate', default=0.001,type=float)
parser.add_argument('--threshold', default=0.1,type=float)

# output path for your prediction
parser.add_argument('--result_path', default='result.csv',)

# put model in the same directory
parser.add_argument('--load_model', default = None)
parser.add_argument('--save_dir', default = 'model/')

# add preEB-related args
parser.add_argument('--glove_dir', default = 'glove/')

args = parser.parse_args()

train_path = 'data/training_set.json'
test_path = 'data/test_set.json'
semi_path = 'data/training_nolabel.txt'

# prepare for load glove eb
# https://blog.keras.io/using-pre-trained-word-embeddings-in-a-keras-model.html
def preEB(dm):
    embeddings_index = {}
    f = open(os.path.join(args.glove_dir, 'vectors.txt'))
    for line in f:
        values = line.split()
        word = values[0]
        coefs = np.asarray(values[1:], dtype='float32')
        embeddings_index[word] = coefs
    f.close()

    print('Found %s word vectors.' % len(embeddings_index))

    embedding_matrix = np.zeros((len(dm.tokenizer.word_index) + 1, args.embedding_dim))
    for word, i in dm.tokenizer.word_index.items():
        embedding_vector = embeddings_index.get(word)
        if embedding_vector is not None:
            # words not found in embedding index will be all-zeros.
            embedding_matrix[i] = embedding_vector

    return embedding_matrix

# build model
def simpleRNN(args,embedding_matrix,word_index):
    inputs = Input(shape=(args.max_length,))

    # Embedding layer
    embedding_inputs = Embedding(len(word_index)+1, 
                                 args.embedding_dim,
                                 weights=[embedding_matrix],
                                 input_length=args.max_length,
                                 trainable=False)(inputs)
    # CNN
    outputs = Conv1D(args.hidden_size//2,2)(embedding_inputs)
    outputs = Conv1D(args.hidden_size,2)(outputs)
    outputs = Flatten()(outputs)

    # DNN layer
    outputs = Dense(args.hidden_size//4, 
                    activation='relu')(outputs)
                    #kernel_regularizer=regularizers.l2(0.1))(RNN_output)
    #outputs = Dropout(dropout_rate)(outputs) 
    outputs = Dense(1, activation='tanh')(outputs)
        
    model =  Model(inputs=inputs,outputs=outputs)

    # optimizer
    adam = Adam()
    print ('compile model...')

    # compile model
    model.compile( loss='mse', optimizer=adam, metrics=['mse'])
    
    return model

def score2class(x):
    x = np.array(x)
    x[x>0] = 1
    x[x<0] = -1
    return x.copy()

def evaluation(pred,ground,mode='mse'):
    
    if mode == 'mse':
        return mean_squared_error(ground, pred)

    pred = score2class(pred)
    ground = score2class(ground)

    if mode == 'f1_micro':
        return f1_score(ground,pred,average='micro')
    elif mode == 'f1_macro':
        return f1_score(ground,pred,average='macro')

def main():
    # limit gpu memory usage
    def get_session(gpu_fraction):
        gpu_options = tf.GPUOptions(per_process_gpu_memory_fraction=gpu_fraction)
        return tf.Session(config=tf.ConfigProto(gpu_options=gpu_options))  
    K.set_session(get_session(args.gpu_fraction))
    
    save_path = os.path.join(args.save_dir,args.model)
    if args.load_model is not None:
        load_path = os.path.join(args.save_dir,args.load_model)

    #####read data#####
    dm = DataManager()
    print ('Loading data...')
    if args.action == 'train':
        dm.add_data('train_data', train_path, True)
    elif args.action == 'semi':
        dm.add_data('train_data', train_path, True)
        dm.add_data('semi_data', semi_path, False)
    elif args.action == 'test':
        dm.add_data('test_data', test_path, True)
    else:
        raise Exception ('Action except for train, semi, and test')
            
    # prepare tokenizer
    print ('get Tokenizer...')
    if args.load_model is not None:
        # read exist tokenizer
        dm.load_tokenizer(os.path.join(load_path,'token.pk'))
    else:
        # create tokenizer on new data
        dm.tokenize(args.vocab_size)
                            
    if not os.path.isdir(save_path):
        os.makedirs(save_path)
    if not os.path.exists(os.path.join(save_path,'token.pk')):
        dm.save_tokenizer(os.path.join(save_path,'token.pk')) 

    # convert to sequences
    dm.to_sequence(args.max_length)

    # prepare glove embedding
    embedding_matrix = preEB(dm)

    # initial model
    print ('initial model...')
    model = simpleRNN(args,embedding_matrix,dm.tokenizer.word_index)    
    model.summary()

    print("args.load_model =", args.load_model)
    if args.load_model is not None:
        if args.action == 'train':
            print ('Warning : load a exist model and keep training')
        path = os.path.join(load_path,'model.h5')
        if os.path.exists(path):
            print ('load model from %s' % path)
            model.load_weights(path)
        else:
            raise ValueError("Can't find the file %s" %path)
    elif args.action == 'test':
        #print ('Warning : testing without loading any model')
        print('args.action is %s'%(args.action))
        path = os.path.join(load_path,'model.h5')
        if os.path.exists(path):
            print ('load model from %s' % path)
            model.load_weights(path)
        else:
            raise ValueError("Can't find the file %s" %path)
        
     # training
    if args.action == 'train':
        (X,Y),(X_val,Y_val) = dm.split_data('train_data', args.val_ratio)
        #earlystopping = EarlyStopping(monitor='val_loss', patience = 3, verbose=1, mode='max')

        save_path = os.path.join(save_path,'model.h5')
        """
        checkpoint = ModelCheckpoint(filepath=save_path, 
                                     verbose=1,
                                     save_best_only=True,
                                     save_weights_only=True,
                                     monitor='val_loss',
                                     mode='max' )
        """
        history = model.fit(X, Y, 
                            validation_data=(X_val, Y_val),
                            epochs=args.nb_epoch, 
                            batch_size=args.batch_size)#,
                            #callbacks=[checkpoint, earlystopping] )

        model.save(save_path) 

    # testing
    elif args.action == 'test' :
        args.val_ratio = 0
        (X,Y), (X_val, Y_val) = dm.split_data('test_data', args.val_ratio)
        pred = model.predict(X)
        scores = model.evaluate(X, Y)
        print("test data scores(loss = mse) = %f" % scores[1])
        print("mse: ",evaluation(pred, Y, 'mse'))
        print("micro: ",evaluation(pred, Y, 'f1_micro'))
        print("macro: ",evaluation(pred, Y, 'f1_macro')) 
    

    # semi-supervised training
    elif args.action == 'semi':
        (X,Y),(X_val,Y_val) = dm.split_data('train_data', args.val_ratio)

        [semi_all_X] = dm.get_data('semi_data')
        earlystopping = EarlyStopping(monitor='val_loss', patience = 3, verbose=1, mode='max')

        save_path = os.path.join(save_path,'model.h5')
        checkpoint = ModelCheckpoint(filepath=save_path, 
                                     verbose=1,
                                     save_best_only=True,
                                     save_weights_only=True,
                                     monitor='val_loss',
                                     mode='max' )
        # repeat 10 times
        for i in range(10):
            # label the semi-data
            semi_pred = model.predict(semi_all_X, batch_size=1024, verbose=True)
            semi_X, semi_Y = dm.get_semi_data('semi_data', semi_pred, args.threshold, args.loss_function)
            semi_X = np.concatenate((semi_X, X))
            semi_Y = np.concatenate((semi_Y, Y))
            print ('-- iteration %d  semi_data size: %d' %(i+1,len(semi_X)))
            # train
            history = model.fit(semi_X, semi_Y, 
                                validation_data=(X_val, Y_val),
                                epochs=2, 
                                batch_size=args.batch_size,
                                callbacks=[checkpoint, earlystopping] )

            if os.path.exists(save_path):
                print ('load model from %s' % save_path)
                model.load_weights(save_path)
            else:
                raise ValueError("Can't find the file %s" %path)

if __name__ == '__main__':
        main()