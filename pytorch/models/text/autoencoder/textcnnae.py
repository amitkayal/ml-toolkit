import torch
import torch.nn as nn
import torch.optim as optim
import torch.nn.functional as F

import numpy as np
import timeit
import random
import datetime


class Token:
    PAD = 0
    UKN = 1
    SOS = 2
    EOS = 3

class ConvMode:
    D1 = 1
    D2 = 2



class Parameters:

    def __init__(self, data_dict):
        for k, v in data_dict.items():
            exec("self.%s=%s" % (k, v))




class TextCnnAE:

    def __init__(self, device, params, criterion):
        # super(SentenceVAE, self).__init__()
        self.params = params
        self.device = device
        self.vocab_size = params.vocab_size
        self.embed_dim = params.embed_dim

        # Embedding layer, shared by encoder and decoder
        self.embedding = nn.Embedding(self.vocab_size, self.embed_dim, max_norm=1, norm_type=2)

        # Calculate the 2-tuples for the kernel sizes (the last one depends on the max_seq_len)
        max_seq_len, kernel_sizes = self.calc_last_seq_len()
        self.params.kernel_sizes[-1] = max_seq_len

        # Create encoder rnn and decoder rnn module
        self.encoder = Encoder(device, self.embedding, params, kernel_sizes)
        self.decoder = Decoder(device, self.embedding, params, kernel_sizes, self.encoder.conv_layers)
        self.encoder.init_weights()
        self.decoder.init_weights()

        # L2-normalize embeddings
        self.embedding.weight.data = F.normalize(self.embedding.weight.data, p=2, dim=1)
        # Just for testing normlization
        #print(np.linalg.norm(self.embedding.weight[0].detach().numpy(), ord=2)) # Should be 1!

        self.encoder.to(device)
        self.decoder.to(device)
        # Create optimizers for encoder and decoder
        self.encoder_lr = self.params.encoder_lr
        self.decoder_lr = self.params.decoder_lr
        self.encoder_optimizer = optim.Adam(self.encoder.parameters(), lr=self.encoder_lr)
        self.decoder_optimizer = optim.Adam(self.decoder.parameters(), lr=self.decoder_lr)
        self.criterion = criterion



    def train(self):
        self.encoder.train()
        self.decoder.train()

    def eval(self):
        self.encoder.eval()
        self.decoder.eval()

    def get_parameter_count(self):
        total_params = sum(p.numel() for p in self.encoder.parameters()) + sum(p.numel() for p in self.decoder.parameters())
        trainable_params = sum(p.numel() for p in self.encoder.parameters() if p.requires_grad) + sum(p.numel() for p in self.decoder.parameters() if p.requires_grad)
        return total_params, trainable_params


    def calc_conv_output_size(self, seq_len, kernel_size, stride, padding):
        return int(((seq_len - kernel_size + 2*padding) / stride) + 1)


    def calc_last_seq_len(self):
        # Calculate the output length of the second-to-last layer
        last_seq_len = self.params.max_seq_len
        for i in range(len(self.params.kernel_sizes) - 1):
            last_seq_len = self.calc_conv_output_size(last_seq_len, self.params.kernel_sizes[i], self.params.strides[i], 0)

        self.params.kernel_sizes[-1] = last_seq_len
        kernel_sizes = []
        for i in range(len(self.params.kernel_sizes)):
            if i == 0:
                kernel_sizes.append((self.params.kernel_sizes[i], self.params.embed_dim))
            else:
                kernel_sizes.append((self.params.kernel_sizes[i], 1))

        return last_seq_len, kernel_sizes



    def update_learning_rates(self, encoder_factor, decoder_factor):
        self.encoder_lr = self.encoder_lr * encoder_factor
        self.decoder_lr = self.decoder_lr * decoder_factor
        self.set_learning_rates(self.encoder_lr, self.decoder_lr)


    def set_learning_rates(self, encoder_lr, decoder_lr):
        self.encoder_lr = encoder_lr
        self.decoder_lr = decoder_lr
        for param_group in self.encoder_optimizer.param_groups:
            param_group['lr'] = encoder_lr
        for param_group in self.decoder_optimizer.param_groups:
            param_group['lr'] = decoder_lr


    def save_models(self, encoder_file_name, decoder_file_name):
        torch.save(self.encoder.state_dict(), encoder_file_name)
        torch.save(self.decoder.state_dict(), decoder_file_name)

    def load_models(self, encoder_file_name, decoder_file_name):
        self.encoder.load_state_dict(torch.load(encoder_file_name))
        self.decoder.load_state_dict(torch.load(decoder_file_name))




    def train_batch(self, inputs):
        # Zero gradients of both optimizers
        self.encoder_optimizer.zero_grad()
        self.decoder_optimizer.zero_grad()

        latent = self.encoder(inputs)
        log_probs = self.decoder(latent)

        losses = [self.criterion(sentence_emb_matrix, word_ids) for sentence_emb_matrix, word_ids in zip(log_probs, inputs)]
        loss = sum([torch.sum(l) for l in losses]) / inputs.shape[0]
        # Backpropagation
        loss.backward()
        # Clip parameters
        torch.nn.utils.clip_grad_norm_(self.encoder.parameters(), self.params.clip)
        torch.nn.utils.clip_grad_norm_(self.decoder.parameters(), self.params.clip)
        #
        self.encoder_optimizer.step()
        self.decoder_optimizer.step()
        # In case of tied weights, this should be True
        #print(TextCnnAE.are_equal_tensors(self.encoder.conv_layers[0].weight, self.decoder.deconv_layers[-1].weight))

        return loss.item()


    def eval_batch(self, inputs):
        latent = self.encoder(inputs)
        log_probs = self.decoder(latent)

        losses = [self.criterion(sentence_emb_matrix, word_ids) for sentence_emb_matrix, word_ids in zip(log_probs, inputs)]
        loss = sum([torch.sum(l) for l in losses]) / inputs.shape[0]

        return loss.item()



    def train_epoch(self, epoch, X_iter, verbatim=False):
        start = timeit.default_timer()
        epoch_loss = 0.0
        num_batches = X_iter.batch_sampler.batch_count()
        for idx, inputs in enumerate(X_iter):
            batch_size = inputs.shape[0]

            # Convert to tensors and move to device
            inputs = inputs.to(self.device)

            # Train batch and get batch loss
            batch_loss = self.train_batch(inputs)
            # Update epoch loss given als batch loss
            epoch_loss += batch_loss

            if verbatim == True:
                print('[{}] Epoch: {} #batches {}/{}, training loss: {:.8f}, learning rates: {:.5f}/{:.5f}'.format(
                    datetime.timedelta(seconds=int(timeit.default_timer() - start)), epoch + 1, idx + 1, num_batches,
                    (batch_loss / ((idx + 1) * batch_size)), self.encoder_lr, self.decoder_lr), end='\r')

        if verbatim == True:
            print()
        return epoch_loss


    def eval_epoch(self, epoch, X_iter, num_batches, verbatim=False):
        start = timeit.default_timer()
        epoch_loss = 0.0
        for idx, inputs in enumerate(X_iter):
            batch_size = inputs.shape[0]

            # Convert to tensors and move to device
            inputs = inputs.to(self.device)

            # Train batch and get batch loss
            batch_loss = self.eval_batch(inputs)
            # Update epoch loss given als batch loss
            epoch_loss += batch_loss

            if verbatim == True:
                print('[{}] Epoch: {} #batches {}/{}, eval loss: {:.8f}'.format(
                    datetime.timedelta(seconds=int(timeit.default_timer() - start)), epoch + 1, idx + 1, num_batches,
                    (batch_loss / ((idx + 1) * batch_size))), end='\r')

        if verbatim == True:
            print()
        return epoch_loss


    def generate(self, inputs, verbatim=False): # Right now, we assume that inputs and lengths are sorted

        latent = self.encoder(inputs)
        log_probs = self.decoder(latent)

        topv, topi = log_probs.topk(k=1, dim=2)
        t = topi.squeeze()
        return t.cpu().numpy()


    @staticmethod
    def are_equal_tensors(a, b):
        if torch.all(torch.eq(a, b)).data.cpu().numpy() == 0:
            return False
        return True



class RegularizationLayer(nn.Module):

    def __init__(self, conv_mode, channels, do_batch_norm=True, dropout_ratio=0.0):
        super(RegularizationLayer, self).__init__()
        self.do_batch_norm = do_batch_norm
        self.dropout_ratio = dropout_ratio
        if do_batch_norm is True:
            if conv_mode == ConvMode.D1:
                self.bn = nn.BatchNorm1d(channels)
            else:
                self.bn = nn.BatchNorm2d(channels)
        if dropout_ratio > 0.0:
            self.dropout = nn.Dropout(p=self.dropout_ratio)

    def forward(self, X):
        if self.do_batch_norm is True:
            X = self.bn(X)
        X = F.relu(X)
        if self.dropout_ratio > 0.0:
            X = self.dropout(X)
        return X



class Encoder(nn.Module):

    def __init__(self, device, embedding, params, kernel_sizes):
        super(Encoder, self).__init__()
        self.params = params
        self.device = device

        # Embedding layer
        self.embedding = embedding

        # Create all L Conv1d layers
        self.conv_layers = nn.ModuleList()

        if self.params.conv_mode == ConvMode.D1:
            in_channels = [self.params.embed_dim] + self.params.num_filters.copy()
        else:
            in_channels = [1] + self.params.num_filters.copy()

        for i in range(len(self.params.kernel_sizes)):
            if self.params.conv_mode == ConvMode.D1:
                conv = nn.Conv1d(in_channels=in_channels[i],
                                 out_channels=self.params.num_filters[i],
                                 kernel_size=self.params.kernel_sizes[i],
                                 stride=self.params.strides[i])
            else:
                conv = nn.Conv2d(in_channels=in_channels[i],
                                 out_channels=self.params.num_filters[i],
                                 kernel_size=kernel_sizes[i],
                                 stride=(self.params.strides[i], 1))

            self.conv_layers.append(conv)

        #print(self.conv_layers)

        # Create all (L-1) regularization layers
        self.reg_layers = nn.ModuleList()
        for i in range(len(self.params.kernel_sizes)-1):
            reg = RegularizationLayer(self.params.conv_mode, self.params.num_filters[i], self.params.do_batch_norm, self.params.dropout_ratio)
            self.reg_layers.append(reg)


    def init_weights(self):
        for m in self.modules():
            if isinstance(m, nn.Conv1d):
                torch.nn.init.xavier_uniform_(m.weight)
            if isinstance(m, nn.Conv2d):
               torch.nn.init.xavier_uniform_(m.weight)
            elif isinstance(m, nn.Embedding):
                torch.nn.init.uniform_(m.weight, -0.001, 0.001)
            elif isinstance(m, nn.Linear):
                torch.nn.init.xavier_uniform_(m.weight)
                m.bias.data.fill_(0.01)



    def forward(self, inputs):
        # Push through embedding layer ==> X.shape = (batch_size, seq_len, embed_dim)
        X = self.embedding(inputs)

        if self.params.conv_mode == ConvMode.D1:
            ##
            ## Conv1d solution
            ##
            # (batch_size, seq_len, embed_dim) ==> (batch_size, embed_dim, seq_len) [since in_channels=embed_dim]
            X = X.transpose(1,2)
            # Push through all (L-1) Conv1d and regularization layers
            for i in range(len(self.conv_layers)-1):
                X = self.conv_layers[i](X)
                X = self.reg_layers[i](X)
            # Push through last Conv1d layer (similating linear layer); no regularization
            X = self.conv_layers[len(self.conv_layers)-1](X)
        else:
            ##
            ## Conv2d solution
            ##
            # Add channel dimensions
            X = X.unsqueeze(1)
            # Push through all (L-1) Conv2d and regularization layers
            for i in range(len(self.conv_layers)-1):
               X = self.conv_layers[i](X)
               X = self.reg_layers[i](X)
            # Push through last Conv2d layer (similating linear layer); no regularization
            X = self.conv_layers[len(self.conv_layers)-1](X)

        # Return last X which is the latent representation
        return X




class Decoder(nn.Module):

    def __init__(self, device, embedding, params, kernel_sizes, conv_layers):
        super(Decoder, self).__init__()
        self.params = params
        self.device = device

        # Embedding layer
        self.embedding = embedding

        # Create all L ConvTranpose1d layers
        self.deconv_layers = nn.ModuleList()

        if self.params.conv_mode == ConvMode.D1:
            out_channels = [self.params.embed_dim] + self.params.num_filters.copy()
        else:
            out_channels = [1] + self.params.num_filters.copy()

        for i in range(len(self.params.kernel_sizes)-1, -1, -1):
            if self.params.conv_mode == ConvMode.D1:
                deconv = nn.ConvTranspose1d(in_channels=self.params.num_filters[i],
                                            out_channels=out_channels[i],
                                            kernel_size=self.params.kernel_sizes[i],
                                            stride=self.params.strides[i],
                                            output_padding=self.params.output_paddings[i])
            else:
                deconv = nn.ConvTranspose2d(in_channels=self.params.num_filters[i],
                                            out_channels=out_channels[i],
                                            kernel_size=kernel_sizes[i],
                                            stride=(self.params.strides[i], 1),
                                            output_padding=(self.params.output_paddings[i], 0))
            # If set, tie weights of conv/deconv layers
            if self.params.tie_weights == True:
                deconv.weight = conv_layers[i].weight
            self.deconv_layers.append(deconv)

        # Create all (L-1) regularization layers
        self.reg_layers = nn.ModuleList()
        for i in range(len(self.params.kernel_sizes)-1, 0, -1):
            reg = RegularizationLayer(self.params.conv_mode, self.params.num_filters[i-1], self.params.do_batch_norm, self.params.dropout_ratio)
            self.reg_layers.append(reg)



    def init_weights(self):
        for m in self.modules():
            if isinstance(m, nn.Conv1d):
                torch.nn.init.xavier_uniform_(m.weight)
            if isinstance(m, nn.Conv2d):
               torch.nn.init.xavier_uniform_(m.weight)
            elif isinstance(m, nn.Embedding):
                torch.nn.init.uniform_(m.weight, -0.001, 0.001)
            elif isinstance(m, nn.Linear):
                torch.nn.init.xavier_uniform_(m.weight)
                m.bias.data.fill_(0.01)



    def forward(self, X):
        batch_size = X.shape[0]
        # Push through (L-1) ConvTranspose1d/ConvTranspose1d & regularization layers
        for i in range(len(self.params.kernel_sizes)-1):
            X = self.deconv_layers[i](X)
            X = self.reg_layers[i](X)
        # Push through last ConvTranspose1d layer (no regularization)
        X = self.deconv_layers[len(self.params.kernel_sizes)-1](X)

        if self.params.conv_mode == ConvMode.D1:
            # L2-normalize along dim=1 given shape (batch_size, EMBED_DIM, seq_len)
            X = F.normalize(X, p=2, dim=1)
            # Just for testing the normalization
            #print(np.linalg.norm(X[0,:,0].detach().numpy(), ord=2)) # Should be 1!
        else:
            # Squeeze (batch_size, IN_CHANNELS, seq_len, embed_dim)
            X = X.squeeze(1)
            # L2-normalize along dim=2 given shape (batch_size, seq_len, EMBED_DIM)
            X = F.normalize(X, p=2, dim=2).transpose(1, 2)

        # Create target "W"
        # * copy weights of embedding matrix
        # * .unsqueeze(0) to add batch dimension
        # * .expand(batch_size, *self.embedding.weight.shape) to copy slice to shape (batch_size, vocab_size, embed_dim)
        #print(self.embedding.weight.shape)
        #print(np.linalg.norm(self.embedding.weight[0,:].detach().numpy(), ord=2))  # Should be 1!
        W = self.embedding.weight.unsqueeze(0).expand(batch_size, *self.embedding.weight.shape)

        prob_logits = torch.bmm(W, X) / self.params.tau # ==> (batch_size, vocab_size, seq_len)
        #print("prob_logits.shape=", prob_logits.shape)
        # Calculate Softmax along dim=1 given shape (batch_size, VOCAB_SIZE, seq_len)
        log_probs = F.log_softmax(prob_logits, dim=1)
        #print("log_probs.shape=", log_probs.shape)
        return log_probs.transpose(1,2)

