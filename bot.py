# -*- coding: utf-8 -*-
import re
from typing import Dict, Any
import numpy as np


class GoalOrientedBot(NNModel):
    """
    """
    def __init__(self,
                 tokenizer,
                 network_parameters,
                 template_path,
                 template_type = "DefaultTemplate",
                 word_vocab = None,
                 bow_embedder = None,
                 embedder = None,
                 slot_filler = None,
                 intent_classifier = None,
                 database = None,
                 api_call_action = None,
                 use_action_mask = False,
                 debug = False,
                 load_path = None,
                 save_path = None,
                 **kwargs):
        super().__init__(load_path=load_path, save_path=save_path, **kwargs)

        self.tokenizer = tokenizer
        self.tracker = tracker
        self.bow_embedder = bow_embedder
        self.embedder = embedder
        self.slot_filler = slot_filler
        self.intent_classifier = intent_classifier
        self.use_action_mask = use_action_mask
        self.debug = debug
        self.word_vocab = word_vocab

        template_path = Path(template_path)
        template_type = gatattr(templ, template_type)
        self.templates = templ.Templates(template_type).load(template_path)
        self.n_actions = len(self.templates)

        self.database = database
        self.api_call_id = None
        if api_call_action is not None:
            self.api_call_id = self.templates.actions.index(api_call_action)

        self.intents = []

        if callable(self.intent_classifier):
            self.intents = list(self.intent_classifier(['hi'])[1][0].keys())

        self.network = self._init_network(network_parameters)

        self.reset()

    def _init_network(sef, params):
        obs_size = 6 + self.tracker.num_features + self.n_actions
        if callable(self.bow_embedder):
            obs_size += len(self.word_vocab)
        if callable(self.embedder):
            obs_size += self.embedder.dim
        if callable(self.intent_classifier):
            obs_size += len(self.intents)
        if 'obs_size' not in params:
            params['obs_size'] = obs_size
        if 'action_size' not in params:
            params['action_size'] = self.n_actions

        attn  params.get('attention_mechanism')
        if attn:
            attn['token_size'] = attn.get('token_size') or self.embedder.dim
            attn['action_as_key'] = attn.get('action_as_key', False)
            attn['intent_as_key'] = attn.get('intent_as_key', False)

            key_size = 0
            if attn['action_as_key']:
                key_size += self.n_actions
            if attn['intent_as_key'] and callable(self.intent_classifier):
                key_size += len(self.intents)
            key_size = key_size or 1
            attn['key_size'] = attn.get('key_size') or key_size

            params['attention_mechanism'] = attn
        return GoalOrientedBotNetwork(**params)

    def _encode_context(self, context, db_result=None):
        #tokenize input
        tokens = self.tokenizer([context.lower().strip()])[0]
        if self.debug:
            pass

        bow_features = []
        if callable(self.bow_embedder):
            bow_features = self.bow_embedder([tokens], self.word_vocab)[0]
            bow_features = bow_features.astype(np.float32)

        emb_features = []
        emb_context = np.array([], dtype=np.float32)
        if callable(self.embedder):
            if self.network.attn:
                if tokens:
                    pad = np.zeros((self.network.attn.max_num_tokens,
                                    self.network.attn.token_size),
                                   dtype=np.float32)
                    sen = np.array(self.embedder([tokens])[0])
                    emb_context = np.concatenate((pad, sen))
                    emb_context = emb_context[-self.network.attn.max_num_tokens:]
                else:
                    emb_context =\
                        np.zeros((self.network.attn.max_num_tokens,
                                  self.network.attn.token_size),
                                 dtype=np.float32)
            else:
                emb_features = self.embedder([tokens], mean=True)[0]
                if np.all(emb_features < 1e-20):
                    emb_dim = self.embedder.dim
                    emb_features = np.fabs(np.random.normal(0,1/emb_dim, emb_dim))

        # Intent features
        intent_features = []
        if callable(self.intent_classifier):
            intent, intent_probs = self.intent_classifier([tokens])
            intent_features = np.array([intent_probs[0][i] for i in self.intents],
                                       dtype=np.float32)
            if self.debug:
                pass

        # 使用 intent 和 act encode 作为 key
        attn_key = np.array([], dtype=np.float32)
        if self.network.attn:
            if self.network.attn.action_as_key:
                attn_key = np.hstack((attn_key, self.prev_action))
            if self.network.attn.intent_as_key:
                attn_key = np.hstack((attn_key, intent_features))
            if len(attn_key) == 0:
                attn_key = np.array([1], dtype=np.float32)

        # text entity features
        if callable(self.slot_filler):
            self.tracker.update_state(self.slot_filler([tokens])[0])
            if self.debug:
                pass

        state_features = self.tracker.get_features()

        # other features
        result_matches_state = 0
        if self.db_result is not None:
            result_matches_state = all(v == self.db_result.get(s)
                                       for s,v in self.tracker.get_state().items()
                                       if v!= 'dontcare') * 1.
        context_features = np.array([bool(db_result) * 1.,
                                     (db_result == {}) *1.,
                                     (self.db_result is None) * 1.,
                                     bool(self.db_result) * 1.,
                                     (self.db_result == {}) * 1.,
                                     result_matches_state],
                                    dtype=np.float32)
        if self.debug:
            #log.debug("context features = {}".format(context_features))
            debug_msg = "num bow features = {}, ".format(len(bow_features)) +\
                        "num emb features = {}, ".format(len(emb_features)) +\
                        "num intent features = {}, ".format(len(intent_features)) +\
                        "num state features = {}, ".format(len(state_features)) +\
                        "num context features = {}, ".format(len(context_features)) +\
                        "prev_action shape = {}".format(len(self.prev_action))
            #log.debug(debug_msg)

        concat_feats = np.hstack((bow_features, emb_features, intent_features,
                                  state_features, context_features, self.prev_action))

        return conccat_feats, emb_context, attn_key

    def _encode_response(self, act):
        return self.templates.actions.index(act)

    def _decode_response(self, action_id):
        """
        """
        template = self.templates.templates[int(action_id)]

        slots = self.tracker.get_state()

        if self.db_result is not None:
            for k,v in self.db_result.items():
                slots[k] = str(v)

        resp = template.generate_text(slots)

        if (self.templates.ttype is templ.DualTemplate) and (action_id == self.api_call_id):
            resp = re.sub('#([A-Za-z]+)', "dontcare", resp).lower()

        if self.debug:
            #log.debug("Pred response = {}".format(resp))
            pass

        return resp


    def _action_mask(self, previous_action):
        '''
        根据之前的 action 和 state tracker
        构建 action 的mask , 限制部分action
        不可用
        '''
        mask = np.ones(self.n_actions, dtype=np.float32)

        if self.use_action_mask:
            known_entities = {**self.tracker.get_state(),**(self.db_result or {})}
            # 遍历每个action ， 取出对应的template 判断 template 中是否包含已在statetracker中entity
            # 如果 entity 不在statetracker 中已经知道的entities 中， 那么为禁止的action
            for a_id in range(self.n_actions):
                tmpl = str(self.templates.templates[a_id])
                for entity in set(re.findall('#([A-Za-z]+)',tmpl)):
                    if entity not in known_entities:
                        mask[a_id] = 0.
        # forbid tow api calls in a row
        if np.any(previous_action):
            prev_act_id = np.argmax(previous_action)
            if prev_act_id == self.api_call_id:
                mask[prev_act_id] = 0.

        return mask

    def train_on_batch(self, x, y):
        b_features, b_u_masks, b_a_masks, b_actions = [], [], [], []
        b_emb_context, b_keys = [], []   # ofr attention

        max_num_utter = max(len(d_contexts) for d_contexts in x)

        for d_contexts, d_responses in zip(x, y):
            self.reset()   # 复位 state tracker 归零， network 状态定义为空的
            if self.debug:
                preds = self._infer_dialog(d_contexts)

            d_features, d_a_masks, d_actions = [], [], []
            d_emb_context, d_key = [], []   # for attention
            for context, reponse in zip(d_contexts, d_responses):
                if context.get('db_result') is not None:
                    self.db_result = context['db_result']

                features, emb_context, key = \
                    self._encode_context(context['text'], context.get('db_result'))

                d_features.append(features)
                d_emb_context.append(emb_context)

                d_key.append(key)
                d_a_masks.append(self._action_mask(self.prev_action))

                action_id = self._encode_response(response['act'])
                d_actions.append(action_id)

                self.prev_action *= 0.
                self.prev_action[action_id] = 1.

                if self.debug:
                    log.debug("True response = `{}`".format(response['text']))
                    if preds[0].lower() != response['text'].lower():
                        log.debug("Pred response = `{}`".format(preds[0]))
                    preds = preds[1:]
                    if d_a_masks[-1][action_id] != 1.:
                        log.warn("True action forbidden by action mask.")

            # padding to max_num_utter
            num_padds = max_num_utter - len(d_contexts)
            d_features.extend([np.zeros_like(d_features[0])] * num_padds)
            d_emb_context.extend([np.zeros_like(d_emb_context[0])] * num_padds)
            d_key.extend([np.zeros_like(d_key[0])] * num_padds)
            d_u_mask = [1] * len(d_contexts) + [0] * num_padds
            d_a_masks.extend([np.zeros_like(d_a_masks[0])] * num_padds)
            d_actions.extend([0] * num_padds)

            b_features.append(d_features)
            b_emb_context.append(d_emb_context)
            b_keys.append(d_key)
            b_u_masks.append(d_u_mask)
            b_a_masks.append(d_a_masks)
            b_actions.append(d_actions)

        self.network.train_on_batch(b_features, b_emb_context, b_keys, b_u_masks,
                                    b_a_masks, b_actions)


    def _infer(self, context, db_result=None, prob=False):
        if db_result is not None:
            self.db_result = db_result

        features, emb_context, key = self._encode_context(context, db_result)
        action_mask = self._action_mask(self.prev_action)

        probs = self.network(
            [[features]], [[emb_context]], [[key]], [[action_mask]],prob=True)

        pred_id = np.argmax(probs)

        if prob:
            self.prev_action = probs
        else:
            self.prev_action *= 0
            self.prev_action[pred_id] = 1

        return self._decode_response(pred_id)

    def _infer_dialog(self, contexts):
        # 重置 环境: db_result 清空  state tracker 归零， network 的状态归零
        self.reset()

        res = []

        for context in contexts:
            if context.get('prev_resp_act') is not None:
                action_id = self._encode_response(context.get('prev_resp_act'))
                # previous action is teacher-forced
                self.prev_action *= 0.
                self.prev_action[action_id] = 1.
            res.append(self._infer(context['text'], context.get('db_result')))

        return res

    def make_api_call(self, slots):
        db_results = []

        if self.database is not None:
            db_slots = {s: v for s, v in slots.items(),
                        if (v != 'dontcare') and (s in self.database.keys)}
            db_slots = self.database([sb_slots])[0]
        else:
            # log.warn("No database specified")
        print("Made api_cell with {}, got {} results.".format(slots, len(db_results)))

        if len(db_results) > 1:
            db_results = [r for r in db_results if r != self.db_result]

        return db_results[0] if db_results else {}


    def __call__(self, batch):

        if isinstance(batch[0], str):
            # 处理单句 的部分
            res = []

            for x in batch:
                pred = self._infer(x)

                prev_act_id = np.argmax(self.prev_action)
                if prev_act_id == self.api_call_id:
                    db_result = self.make_api_call(self.tracker.get_state())
                    res.append(self._infer(x, db_result=db_result))
                else:
                    res.append(pred)
            return res

        return [self._infer_dialog(x) for x in batch]

    def reset(self):
        self.tracker.reset_state()       # 状态追踪归零
        self.db_result = None            # 数据库查询信息置为空
        self.prev_action = np.zeros(self.n_actions, dtype=np.float32)   # 最近一次的动作置为0
        self.network.reset_state()     # 网络初始化状态置为0
        if self.debug:
            print("bot reset")

    def process_event(self, *args, **kwargs):
        self.network.process_event(*args, **kwargs)

    def save(self):
        self.network.save()

    def shutdown(self):
        self.network.shutdown()
        self.slot_filler.shutdown()

    def load(self):
        pass




